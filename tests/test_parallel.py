import threading
import time
import unittest


class OrderedParallelTests(unittest.TestCase):
    def test_run_ordered_parallel_preserves_result_order_and_limits_workers(self):
        from ttml_metadata.parallel import run_ordered_parallel

        active = 0
        max_active = 0
        lock = threading.Lock()

        def work(value: int) -> str:
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            try:
                time.sleep(0.03 if value == 0 else 0.01)
                return f"item-{value}"
            finally:
                with lock:
                    active -= 1

        result = run_ordered_parallel([0, 1, 2], work, max_workers=2)

        self.assertEqual(result, ["item-0", "item-1", "item-2"])
        self.assertEqual(max_active, 2)

    def test_run_ordered_parallel_preserves_none_results(self):
        from ttml_metadata.parallel import run_ordered_parallel

        result = run_ordered_parallel([0, 1, 2], lambda value: None if value == 1 else value, max_workers=2)

        self.assertEqual(result, [0, None, 2])


if __name__ == "__main__":
    unittest.main()
