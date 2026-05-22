from smbus2 import SMBus
import time
import threading
import numpy as np
import hrcalc
from collections import deque

ADDR = 0x57

# Registers
REG_INTR_STATUS_1   = 0x00
REG_INTR_STATUS_2   = 0x01
REG_INTR_ENABLE_1   = 0x02
REG_INTR_ENABLE_2   = 0x03
REG_FIFO_WR_PTR     = 0x04
REG_OVF_COUNTER     = 0x05
REG_FIFO_RD_PTR     = 0x06
REG_FIFO_DATA       = 0x07
REG_FIFO_CONFIG     = 0x08
REG_MODE_CONFIG     = 0x09
REG_SPO2_CONFIG     = 0x0A
REG_LED1_PA         = 0x0C   # RED
REG_LED2_PA         = 0x0D   # IR
REG_MULTI_LED_CTRL1 = 0x11
REG_MULTI_LED_CTRL2 = 0x12
REG_PART_ID         = 0xFF
REG_REV_ID          = 0xFE

MODE_SPO2 = 0x03


class StableBeatTracker:
    """
    Tracks accepted beat timestamps and produces a stable BPM.
    Uses interval filtering + median smoothing to reject dips/spikes.
    """
    def __init__(self):
        self.beat_times = deque(maxlen=12)
        self.bpm_history = deque(maxlen=5)
        self.display_bpm = None
        self.last_peak_time = 0.0

    def reset(self):
        self.beat_times.clear()
        self.bpm_history.clear()
        self.display_bpm = None
        self.last_peak_time = 0.0

    def process_peak(self, now):
        if self.last_peak_time != 0.0:
            dt = now - self.last_peak_time

            # Reject false double-counts / unrealistic spikes
            if dt < 0.48:   # ~125 BPM upper bound
                return

            # Reject missed-beat style very long gaps for resting measurement
            if dt > 1.30:   # ~46 BPM lower bound
                self.last_peak_time = now
                return

        self.last_peak_time = now
        self.beat_times.append(now)

        # Need several accepted beats before showing BPM
        if len(self.beat_times) < 3:
            self.display_bpm = None
            return

        intervals = np.diff(np.array(self.beat_times, dtype=float))
        if len(intervals) < 2:
            self.display_bpm = None
            return

        bpm_values = 60.0 / intervals

        # Tight plausible range for resting finger test
        bpm_values = bpm_values[(bpm_values >= 45) & (bpm_values <= 130)]
        if len(bpm_values) < 2:
            self.display_bpm = None
            return

        bpm_med = float(np.median(bpm_values))
        self.bpm_history.append(bpm_med)

        if len(self.bpm_history) >= 3:
            self.display_bpm = float(np.mean(self.bpm_history))
        else:
            self.display_bpm = None


class HeartRateMonitor:
    def __init__(
        self,
        print_result=True,
        print_interval=0.25,
        window_size=120,              # ~4 sec at 100 Hz
        finger_lost_timeout=0.25,
        debug=False
    ):
        self.print_result = print_result
        self.print_interval = float(print_interval)
        self.window_size = int(window_size)
        self.finger_lost_timeout = float(finger_lost_timeout)
        self.debug = bool(debug)

        self.sensor_found = False
        self.finger_detected = False
        self.bpm = None
        self.spo2 = None

        self._stop = threading.Event()
        self._thread = None

        # Raw sensor windows
        self._ir = deque(maxlen=self.window_size)
        self._red = deque(maxlen=self.window_size)

        # For BPM processing
        self._ir_centered = deque(maxlen=self.window_size)
        self._ir_filtered = deque(maxlen=self.window_size)

        self._last_finger_seen = 0.0
        self._beat_tracker = StableBeatTracker()
        self._last_spo2_calc = 0.0

    def _w(self, bus, reg, val):
        bus.write_byte_data(ADDR, reg, val & 0xFF)

    def _r(self, bus, reg):
        return bus.read_byte_data(ADDR, reg)

    def _read_fifo_sample(self, bus):
        data = bus.read_i2c_block_data(ADDR, REG_FIFO_DATA, 6)
        red = ((data[0] << 16) | (data[1] << 8) | data[2]) & 0x3FFFF
        ir  = ((data[3] << 16) | (data[4] << 8) | data[5]) & 0x3FFFF
        return red, ir

    def _check_sensor(self, bus):
        try:
            part_id = self._r(bus, REG_PART_ID)
            rev_id = self._r(bus, REG_REV_ID)
            self.sensor_found = True
            print(f"MAX30102 detected: PART_ID=0x{part_id:02X}, REV_ID=0x{rev_id:02X}")
            return True
        except Exception as e:
            self.sensor_found = False
            print(f"MAX30102 not detected: {e}")
            return False

    def _init_sensor(self, bus):
        # Reset
        self._w(bus, REG_MODE_CONFIG, 0x40)
        time.sleep(0.2)

        # Clear interrupt status
        try:
            self._r(bus, REG_INTR_STATUS_1)
            self._r(bus, REG_INTR_STATUS_2)
        except Exception:
            pass

        # Disable interrupts for polling mode
        self._w(bus, REG_INTR_ENABLE_1, 0x00)
        self._w(bus, REG_INTR_ENABLE_2, 0x00)

        # Reset FIFO pointers
        self._w(bus, REG_FIFO_WR_PTR, 0x00)
        self._w(bus, REG_OVF_COUNTER, 0x00)
        self._w(bus, REG_FIFO_RD_PTR, 0x00)

        # FIFO averaging + rollover
        self._w(bus, REG_FIFO_CONFIG, 0x4F)

        # ADC range 4096nA, 100Hz, 411us
        self._w(bus, REG_SPO2_CONFIG, 0x27)

        # Moderate LED currents; adjust only if really needed
        self._w(bus, REG_LED1_PA, 0x1F)   # RED
        self._w(bus, REG_LED2_PA, 0x24)   # IR

        # Slot1 = RED, Slot2 = IR
        self._w(bus, REG_MULTI_LED_CTRL1, 0x21)
        self._w(bus, REG_MULTI_LED_CTRL2, 0x00)

        # SpO2 mode
        self._w(bus, REG_MODE_CONFIG, MODE_SPO2)
        time.sleep(0.1)

    def _clear_outputs(self):
        self.finger_detected = False
        self.bpm = None
        self.spo2 = None
        self._beat_tracker.reset()

    def _finger_present_now(self):
        if len(self._ir) < 50:
            return False

        arr = np.array(self._ir, dtype=float)
        ir_mean = float(np.mean(arr))
        ir_amp = float(np.max(arr) - np.min(arr))
        ir_std = float(np.std(arr))

        # Slightly stricter than before to reject stale/weak contact
        present = (ir_mean > 5000) and (ir_amp > 220) and (ir_std > 50)

        if present:
            self._last_finger_seen = time.time()

        return present

    def _finger_still_present(self):
        if self._finger_present_now():
            return True

        return (time.time() - self._last_finger_seen) <= self.finger_lost_timeout

    def _append_filtered_sample(self, ir):
        """
        Build a BPM-friendly waveform:
        1) remove DC using a slow moving average
        2) smooth the centered signal
        """
        self._ir.append(ir)

        # DC removal using slower mean
        slow_n = 25
        if len(self._ir) < slow_n:
            dc = float(np.mean(self._ir))
        else:
            dc = float(np.mean(list(self._ir)[-slow_n:]))

        centered = float(ir - dc)
        self._ir_centered.append(centered)

        # Smooth centered signal
        fast_n = 7
        if len(self._ir_centered) < fast_n:
            filt = float(np.mean(self._ir_centered))
        else:
            filt = float(np.mean(list(self._ir_centered)[-fast_n:]))

        self._ir_filtered.append(filt)

    def _detect_peak_and_update_bpm(self):
        if len(self._ir_filtered) < 40:
            self.bpm = self._beat_tracker.display_bpm
            return

        y = np.array(self._ir_filtered, dtype=float)

        recent = y[-100:] if len(y) >= 100 else y
        local_mean = float(np.mean(recent))
        local_std = float(np.std(recent))

        # If pulsation is weak, don't accept peaks
        if local_std < 20:
            self.bpm = self._beat_tracker.display_bpm
            return

        # Last three smoothed, centered samples
        a, b, c = y[-3], y[-2], y[-1]

        # Peak prominence threshold
        threshold = local_mean + 0.9 * local_std
        prominence = b - local_mean

        is_peak = (
            (b > a) and
            (b > c) and
            (b > threshold) and
            (prominence > 0.0)
        )

        if is_peak:
            self._beat_tracker.process_peak(time.time())

        self.bpm = self._beat_tracker.display_bpm

    def _update_spo2(self):
        """
        Keep hrcalc for SpO2, but do not run it too often.
        """
        now = time.time()
        if (now - self._last_spo2_calc) < 1.0:
            return

        self._last_spo2_calc = now

        try:
            bpm_tmp, valid_bpm, spo2, valid_spo2 = hrcalc.calc_hr_and_spo2(
                list(self._ir), list(self._red)
            )

            if valid_spo2 and 70 <= spo2 <= 100:
                self.spo2 = float(spo2)
            else:
                self.spo2 = None

        except Exception:
            self.spo2 = None

    def _sensor_loop(self):
        last_print = 0.0

        with SMBus(1) as bus:
            if not self._check_sensor(bus):
                return

            self._init_sensor(bus)

            while not self._stop.is_set():
                try:
                    wr = self._r(bus, REG_FIFO_WR_PTR) & 0x1F
                    rd = self._r(bus, REG_FIFO_RD_PTR) & 0x1F
                    num = (wr - rd) & 0x1F

                    if num == 0:
                        time.sleep(0.01)
                        continue

                    for _ in range(num):
                        red, ir = self._read_fifo_sample(bus)
                        self._red.append(red)
                        self._append_filtered_sample(ir)

                    if len(self._ir) < self.window_size:
                        self.finger_detected = False
                        self.bpm = None
                        self.spo2 = None
                    else:
                        self.finger_detected = self._finger_still_present()

                        if not self.finger_detected:
                            self._clear_outputs()
                        else:
                            self._detect_peak_and_update_bpm()
                            self._update_spo2()

                    now = time.time()
                    if self.print_result and (now - last_print) >= self.print_interval:
                        if len(self._ir) < self.window_size:
                            print(f"Collecting samples... {len(self._ir)}/{self.window_size}")
                        elif not self.finger_detected:
                            print("Finger not detected")
                        elif self.bpm is None:
                            spo2_text = f"{self.spo2:.1f}" if self.spo2 is not None else "---"
                            print(f"Finger detected | Stabilizing BPM... | SpO2={spo2_text}")
                        else:
                            spo2_text = f"{self.spo2:.1f}" if self.spo2 is not None else "---"
                            print(f"BPM={self.bpm:.1f} | SpO2={spo2_text}")

                        if self.debug and len(self._ir) >= 100:
                            arr = np.array(self._ir, dtype=float)
                            filt = np.array(self._ir_filtered, dtype=float)
                            print(
                                f"[DBG] IR mean={np.mean(arr):.0f} "
                                f"IR amp={np.max(arr)-np.min(arr):.0f} "
                                f"IR std={np.std(arr):.0f} "
                                f"FILT std={np.std(filt):.1f}"
                            )

                        last_print = now

                    time.sleep(0.01)

                except OSError as e:
                    print(f"I2C ERROR: {e} (retrying)")
                    time.sleep(0.2)
                except Exception as e:
                    print(f"ERROR: {e}")
                    time.sleep(0.2)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._sensor_loop, daemon=True)
        self._thread.start()

    def stop(self, timeout=2.0):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

        self._clear_outputs()
        self.sensor_found = False
        self._ir.clear()
        self._red.clear()
        self._ir_centered.clear()
        self._ir_filtered.clear()


if __name__ == "__main__":
    hr = HeartRateMonitor(
        print_result=True,
        print_interval=1.0,
        window_size=400,
        finger_lost_timeout=1.0,
        debug=False
    )

    hr.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        hr.stop()
        print("Stopped.")
