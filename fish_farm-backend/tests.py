from django.test import TestCase

# Create your tests here.
from rest_framework.test import APITestCase


class HealthCheckAPITest(APITestCase):

    def test_health_check(self):
        response = self.client.get("/api/health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "ok")
        self.assertEqual(
            response.data["service"],
            "fish-farm-backend",
        )