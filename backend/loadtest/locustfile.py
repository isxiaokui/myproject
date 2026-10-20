import os

from locust import HttpUser, between, task


class FlowerAssistantUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        token = os.getenv("LOAD_TEST_TOKEN", "").strip()
        if not token:
            raise RuntimeError("Set LOAD_TEST_TOKEN to a valid bearer token before running this test.")
        self.client.headers.update({"Authorization": f"Bearer {token}"})

    @task
    def list_conversations(self):
        self.client.get("/conversations", name="GET /conversations")
