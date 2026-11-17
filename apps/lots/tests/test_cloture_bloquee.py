"""La route PATCH /api/v1/lots/{id}/cloturer/ est bloquée (voir apps/lots/viewsets.py)."""
from django.test import SimpleTestCase
from django.urls import NoReverseMatch, Resolver404, resolve, reverse


class ClotureLotBloqueeTests(SimpleTestCase):
    def test_la_route_n_est_plus_generee(self):
        with self.assertRaises(NoReverseMatch):
            reverse("lot-cloturer", args=[1])
        with self.assertRaises(Resolver404):
            resolve("/api/v1/lots/1/cloturer/")
