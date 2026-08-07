"""The worker-thread fetch handoff.

app.py needs firmware to import, so this exercises the same logic against a
stand-in mirroring _start_poll / _fetch_worker / _apply_result. What it pins is
the contract: the worker only ever writes the result slot and the busy flag, the
main task does everything else, and no failure path can leave polling wedged.

The cost this replaces was measured in the simulator: fetching on the main task
stalled the UI for 5.4 seconds at a time, which on real hardware meant button
presses were simply missed.
"""

import unittest

from skyscope import adsb, conf as C, model

from tests.test_view import OBS, contact_at

FETCH_WATCHDOG_MS = 45000


class FakeApp:
    """Mirror of the fetch handoff in app.FlightRadarApp."""

    def __init__(self, provider, threads=True):
        self.cfg = C.validate({})
        self.provider = provider
        self.threads = threads
        self.snapshot = model.Snapshot(state=model.STATE_IDLE)
        self.errors = []
        self.spawned_inline = False
        self._fetch_busy = False
        self._fetch_result = None
        self._fetch_started_ms = 0
        self.now = 0

    # -- the code under test --------------------------------------------

    def _start_poll(self):
        if self._fetch_busy:
            return
        self._fetch_busy = True
        self._fetch_started_ms = self.now
        cfg = self.cfg
        lat, lon = C.location_of(cfg)
        job = (self.provider, lat, lon, cfg["radius_km"], cfg["max_aircraft"])
        if self.threads:
            return  # a real thread would run _fetch_worker(job) later
        self.spawned_inline = True
        self._fetch_worker(job)

    def _fetch_worker(self, job):
        provider, lat, lon, radius_km, max_aircraft = job
        try:
            contacts, total = provider.poll(lat, lon, radius_km)
            contacts = model.prepare(contacts, lat, lon, radius_km, max_aircraft)
            self._fetch_result = ("ok", contacts, total, lat, lon, radius_km)
        except adsb.ProviderError as exc:
            self._fetch_result = ("error", exc.message)
        except MemoryError:
            self._fetch_result = ("error", "OUT OF MEMORY")
        except Exception as exc:
            self._fetch_result = ("error", adsb._short_error(exc))
        finally:
            self._fetch_busy = False

    def _apply_result(self, result):
        if result[0] != "ok":
            self.errors.append(result[1])
            return
        _kind, contacts, total, lat, lon, radius_km = result
        self.snapshot = model.Snapshot(
            contacts=contacts, ts_ms=self.now, state=model.STATE_OK,
            total=total, obs_lat=lat, obs_lon=lon, radius_km=radius_km,
        )

    def pump(self):
        """One pass of the main loop's fetch bookkeeping."""
        result = self._fetch_result
        if result is not None:
            self._fetch_result = None
            self._apply_result(result)
        elif (self._fetch_busy
              and self.now - self._fetch_started_ms > FETCH_WATCHDOG_MS):
            self._fetch_busy = False
            self.errors.append("FETCH TIMED OUT")

    def run_worker(self):
        """Stand in for the thread actually running."""
        cfg = self.cfg
        lat, lon = C.location_of(cfg)
        self._fetch_worker((self.provider, lat, lon, cfg["radius_km"],
                            cfg["max_aircraft"]))


class GoodProvider:
    def __init__(self, count=3):
        self.calls = 0
        self.count = count

    def poll(self, lat, lon, radius_km, timeout=10, user_agent=None):
        self.calls += 1
        contacts = [contact_at("AC%d" % i, i * 40.0, 5.0 + i) for i in range(self.count)]
        return contacts, self.count


class FailingProvider:
    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    def poll(self, *a, **k):
        self.calls += 1
        raise self.exc


class TestHandoff(unittest.TestCase):
    def test_start_returns_without_the_result(self):
        app = FakeApp(GoodProvider())
        app._start_poll()
        # The point of the exercise: control comes straight back.
        self.assertTrue(app._fetch_busy)
        self.assertIsNone(app._fetch_result)
        self.assertEqual(app.snapshot.state, model.STATE_IDLE)

    def test_result_is_applied_on_the_main_task(self):
        app = FakeApp(GoodProvider(count=4))
        app._start_poll()
        app.run_worker()
        self.assertFalse(app._fetch_busy)
        app.pump()
        self.assertEqual(app.snapshot.state, model.STATE_OK)
        self.assertEqual(app.snapshot.total, 4)
        self.assertEqual(len(app.snapshot.contacts), 4)

    def test_no_second_fetch_while_one_is_in_flight(self):
        # The guard only has to hold while a worker is actually running. On the
        # inline fallback the fetch has already finished by the time _start_poll
        # returns, and the poll schedule is what spaces those out.
        provider = GoodProvider()
        app = FakeApp(provider, threads=True)
        app._start_poll()
        app._start_poll()
        app._start_poll()
        self.assertTrue(app._fetch_busy)
        app.run_worker()
        self.assertEqual(provider.calls, 1)

    def test_a_second_fetch_can_start_once_the_first_lands(self):
        provider = GoodProvider()
        app = FakeApp(provider, threads=True)
        app._start_poll()
        app.run_worker()
        app.pump()
        app._start_poll()
        app.run_worker()
        self.assertEqual(provider.calls, 2)

    def test_inline_fallback_still_produces_a_result(self):
        app = FakeApp(GoodProvider(), threads=False)
        app._start_poll()
        self.assertTrue(app.spawned_inline)
        app.pump()
        self.assertEqual(app.snapshot.state, model.STATE_OK)


class TestFailurePaths(unittest.TestCase):
    def _run(self, exc):
        app = FakeApp(FailingProvider(exc), threads=False)
        app._start_poll()
        app.pump()
        return app

    def test_provider_error_surfaces_its_message(self):
        app = self._run(adsb.ProviderError("RATE LIMITED", 429))
        self.assertEqual(app.errors, ["RATE LIMITED"])

    def test_memory_error(self):
        self.assertEqual(self._run(MemoryError()).errors, ["OUT OF MEMORY"])

    def test_unexpected_exception_does_not_escape_the_worker(self):
        app = self._run(ValueError("boom"))
        self.assertEqual(len(app.errors), 1)

    def test_every_failure_clears_the_busy_flag(self):
        for exc in (adsb.ProviderError("X"), MemoryError(), ValueError("y"),
                    OSError()):
            app = FakeApp(FailingProvider(exc), threads=False)
            app._start_poll()
            self.assertFalse(app._fetch_busy, exc)

    def test_a_lost_worker_is_recovered_by_the_watchdog(self):
        # The thread died without unwinding: busy stays set and nothing arrives.
        app = FakeApp(GoodProvider())
        app._start_poll()
        app.pump()
        self.assertTrue(app._fetch_busy)      # still waiting, correctly
        app.now = FETCH_WATCHDOG_MS + 1
        app.pump()
        self.assertFalse(app._fetch_busy)
        self.assertEqual(app.errors, ["FETCH TIMED OUT"])

    def test_watchdog_does_not_fire_on_a_slow_but_live_fetch(self):
        app = FakeApp(GoodProvider())
        app._start_poll()
        app.now = FETCH_WATCHDOG_MS - 1
        app.pump()
        self.assertTrue(app._fetch_busy)
        self.assertEqual(app.errors, [])


class TestQuerySnapshotting(unittest.TestCase):
    def test_the_result_carries_the_location_it_was_asked_for(self):
        # Settings can change while a fetch is in flight; the snapshot has to
        # record where the aircraft were actually measured from.
        app = FakeApp(GoodProvider(), threads=False)
        asked_lat, asked_lon = C.location_of(app.cfg)
        app._start_poll()
        app.cfg["location"] = {"name": "Elsewhere", "lat": 0.0, "lon": 0.0,
                               "source": "manual"}
        app.pump()
        self.assertAlmostEqual(app.snapshot.obs_lat, asked_lat)
        self.assertAlmostEqual(app.snapshot.obs_lon, asked_lon)

    def test_contacts_are_reduced_in_the_worker_not_the_main_task(self):
        # prepare() is the expensive part after parsing; it belongs off the UI.
        app = FakeApp(GoodProvider(count=10), threads=False)
        app.cfg["max_aircraft"] = 3
        app._start_poll()
        kind, contacts, total = app._fetch_result[0:3]
        self.assertEqual(kind, "ok")
        self.assertEqual(len(contacts), 3)
        self.assertEqual(total, 10)


class TestParserCost(unittest.TestCase):
    """The scanner must not re-scan the payload for every marker."""

    def test_scanning_stays_proportional_to_the_payload(self):
        class CountingStr(str):
            total = 0

            def find(self, sub, start=0):
                at = str.find(self, sub, start)
                CountingStr.total += (len(self) - start) if at < 0 else (at - start)
                return at

            def __getitem__(self, k):
                return CountingStr(str.__getitem__(self, k))

        body = '{"ac":[' + ",".join(
            '{"hex":"%06x","flight":"TEST%03d  ","t":"A320","r":"G-TEST",'
            '"alt_baro":%d,"gs":420.5,"track":123.4,"baro_rate":0,'
            '"squawk":"7000","lat":51.6,"lon":0.7,"seen_pos":0.5,'
            '"mlat":[],"tisb":[],"seen":0.5}' % (i, i, 30000 + i)
            for i in range(120)
        ) + '],"total":120}'

        CountingStr.total = 0
        contacts, total = adsb.parse(CountingStr(body))
        self.assertEqual(total, 120)
        ratio = CountingStr.total / len(body)
        # Four markers crossing the text once each is the target; the old
        # version re-found all four every step and scanned ~90x the payload.
        self.assertLess(ratio, 8.0, "scanned %.1fx the payload" % ratio)


class TestPrepareOffThread(unittest.TestCase):
    def test_prepare_caps_and_sorts(self):
        contacts = [contact_at("AC%02d" % i, i * 11.0, 30.0 - i * 0.5)
                    for i in range(20)]
        kept = model.prepare(contacts, OBS[0], OBS[1], 100.0, 5)
        self.assertEqual(len(kept), 5)
        self.assertEqual([c.icao for c in kept],
                         sorted([c.icao for c in kept],
                                key=lambda n: [k.dist_km for k in kept
                                               if k.icao == n][0]))


if __name__ == "__main__":
    unittest.main()
