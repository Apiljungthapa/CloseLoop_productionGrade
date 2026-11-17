import hashlib
import hmac
import unittest
from unittest import mock

import requests

from app.config import settings
from app.services import hrms_service as svc


class _Resp:
    def __init__(self, status=200, body=None, bad_json=False):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._body = body
        self._bad = bad_json

    def json(self):
        if self._bad:
            raise ValueError("no json")
        return self._body


class HrmsServiceTests(unittest.TestCase):
    def setUp(self):
        svc.clear_cache()
        self._saved = (settings.HRMS_TENANT_ID, settings.HRMS_SECRET, settings.HRMS_CACHE_SECONDS)
        settings.HRMS_TENANT_ID, settings.HRMS_SECRET = "17", "s3cret"
        settings.HRMS_CACHE_SECONDS = 60

    def tearDown(self):
        settings.HRMS_TENANT_ID, settings.HRMS_SECRET, settings.HRMS_CACHE_SECONDS = self._saved
        svc.clear_cache()

    def test_signature_is_hex_hmac_sha256_of_tenant_id(self):
        expected = hmac.new(b"s3cret", b"17", hashlib.sha256).hexdigest()
        self.assertEqual(svc.generate_signature("17", "s3cret"), expected)

    def test_normalize_sorts_dedupes_and_skips_bad_rows(self):
        data = [
            {"id": 2, "name": "zeta"}, {"id": 1, "name": " Alpha "},
            {"id": 1, "name": "Alpha again"}, {"id": 3, "name": ""},
            {"id": None, "name": "No id"}, "junk",
        ]
        self.assertEqual(
            svc.normalize_projects(data),
            [{"id": 1, "name": "Alpha"}, {"id": 2, "name": "zeta"}],
        )

    def test_normalize_accepts_wrapped_list_and_rejects_other_shapes(self):
        self.assertEqual(svc.normalize_projects({"projects": [{"id": 1, "name": "A"}]}),
                         [{"id": 1, "name": "A"}])
        with self.assertRaises(svc.HrmsError):
            svc.normalize_projects("nope")

    def test_not_configured(self):
        settings.HRMS_SECRET = ""
        with self.assertRaises(svc.HrmsNotConfigured):
            svc.get_projects()

    def test_fetch_sends_signed_header_and_caches(self):
        body = {"data": [{"id": 4, "name": "Hrms Service"}]}
        with mock.patch.object(svc.requests, "get", return_value=_Resp(200, body)) as get:
            first = svc.get_projects(now=100.0)
            second = svc.get_projects(now=120.0)  # within the 60s cache
        self.assertEqual(first, [{"id": 4, "name": "Hrms Service"}])
        self.assertEqual(second, first)
        self.assertEqual(get.call_count, 1)
        url = get.call_args.args[0]
        self.assertTrue(url.endswith("/v1/external/projects"))
        header = get.call_args.kwargs["headers"]["x-tenant-id"]
        self.assertEqual(header, "17." + svc.generate_signature("17", "s3cret"))

    def test_cache_expires(self):
        body = {"data": [{"id": 1, "name": "A"}]}
        with mock.patch.object(svc.requests, "get", return_value=_Resp(200, body)) as get:
            svc.get_projects(now=100.0)
            svc.get_projects(now=200.0)  # past the 60s TTL
        self.assertEqual(get.call_count, 2)

    def test_upstream_failures_become_hrms_error(self):
        for resp in (_Resp(500, {"x": 1}), _Resp(200, {"no": "data"}), _Resp(200, bad_json=True)):
            svc.clear_cache()
            with mock.patch.object(svc.requests, "get", return_value=resp):
                with self.assertRaises(svc.HrmsError):
                    svc.get_projects()
        svc.clear_cache()
        with mock.patch.object(svc.requests, "get", side_effect=requests.Timeout()):
            with self.assertRaises(svc.HrmsError):
                svc.get_projects()


ACTIVITIES_PAYLOAD = [
    {"id": 31, "name": "Activity 2", "activityType": 0, "description": "plain"},
    {"id": 33, "name": " Have Sub Activity ", "activityType": 1, "description": None,
     "subActivities": [
         {"id": 60, "name": "First sub activity", "key": "#1231", "dataType": 0},
         {"id": 66, "name": "Second", "key": "", "dataType": 1},
         {"id": 60, "name": "dup", "key": "x"},
         {"id": 70, "name": "  "},
         "junk",
     ]},
    {"id": 31, "name": "dup of 31"},
    {"id": 37, "name": "Type 1 but no subs", "activityType": 1},
    {"name": "no id"},
]


class HrmsActivitiesTests(HrmsServiceTests):
    def test_normalize_activities_keeps_order_and_subs(self):
        out = svc.normalize_activities(ACTIVITIES_PAYLOAD)
        self.assertEqual([a["id"] for a in out], [31, 33, 37])
        self.assertEqual(out[0], {"id": 31, "name": "Activity 2", "description": "plain", "sub_activities": []})
        self.assertEqual(out[1]["name"], "Have Sub Activity")
        self.assertEqual(
            out[1]["sub_activities"],
            [{"id": 60, "name": "First sub activity", "key": "#1231", "description": None, "data_type": 0},
             {"id": 66, "name": "Second", "key": None, "description": None, "data_type": 1}],
        )
        self.assertEqual(out[2]["sub_activities"], [])

    def test_normalize_activities_rejects_other_shapes(self):
        with self.assertRaises(svc.HrmsError):
            svc.normalize_activities("nope")

    def test_activities_use_their_own_endpoint_and_cache(self):
        projects = {"data": [{"id": 4, "name": "P"}]}
        acts = {"data": ACTIVITIES_PAYLOAD}
        def fake_get(url, **kw):
            return _Resp(200, acts if url.endswith("/activities") else projects)
        with mock.patch.object(svc.requests, "get", side_effect=fake_get) as get:
            svc.get_projects(now=1.0)
            a1 = svc.get_activities(now=2.0)
            a2 = svc.get_activities(now=3.0)
            svc.get_projects(now=4.0)
        self.assertEqual(a1, a2)
        self.assertEqual(get.call_count, 2)  # one per endpoint, then cache hits
        self.assertTrue(get.call_args_list[1].args[0].endswith("/v1/external/activities"))


class HrmsRouteTests(unittest.TestCase):
    def test_routes_registered(self):
        from app.routers.hrms import router
        paths = [r.path for r in router.routes]
        self.assertIn("/api/hrms/projects", paths)
        self.assertIn("/api/hrms/activities", paths)


if __name__ == "__main__":
    unittest.main()
