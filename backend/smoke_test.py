from __future__ import annotations

import argparse
import json
import os
import sys
import time
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable


ADMIN_PHONE = "+201094321642"
SALES_PHONE = "+201111111111"
OWNER_PHONE = "+201012345678"
DEFAULT_DEMO_RESTAURANT_ID = "demo-restaurant"
DEV_OTP_BYPASS = os.getenv("DEV_OTP_BYPASS", "956956").strip() or "956956"


class SmokeTestError(RuntimeError):
    pass


class ApiError(RuntimeError):
    def __init__(self, status_code: int, body: Any):
        self.status_code = status_code
        self.body = body
        super().__init__(f"API {status_code}: {body}")


@dataclass
class Session:
    token: str
    role: str
    profile: dict[str, Any]


class ApiClient:
    def __init__(self, base_url: str, token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def with_token(self, token: str) -> "ApiClient":
        return ApiClient(self.base_url, token)

    def request(
        self,
        method: str,
        path: str,
        *,
        data: Any | None = None,
        headers: dict[str, str] | None = None,
        query: dict[str, Any] | None = None,
        raw: bool = False,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if query:
            cleaned_query = {key: value for key, value in query.items() if value is not None}
            if cleaned_query:
                url = f"{url}?{urllib.parse.urlencode(cleaned_query)}"

        payload = None if data is None else json.dumps(data).encode("utf-8")
        request_headers = dict(headers or {})
        if payload is not None:
            request_headers["Content-Type"] = "application/json"
        if self.token:
            request_headers["Authorization"] = f"Bearer {self.token}"

        req = urllib.request.Request(url, data=payload, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                body = response.read()
                if raw:
                    return body
                if not body:
                    return None
                return json.loads(body.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = body
            raise ApiError(exc.code, parsed) from exc

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> Any:
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> Any:
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self.request("DELETE", path, **kwargs)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeTestError(message)


def log_pass(message: str) -> None:
    print(f"[PASS] {message}")


def log_warn(message: str) -> None:
    print(f"[WARN] {message}")


def unique_phone(prefix: str = "13") -> str:
    suffix = int(time.time() * 1000) % 100_000_000
    return f"+20{prefix}{suffix:08d}"


def login_with_bypass(api: ApiClient, phone: str, expected_role: str) -> Session:
    send_result = api.post("/auth/send-otp", data={"phone": phone})
    check(send_result.get("success") is True, f"OTP send should succeed for {phone}")
    check("devOtp" not in send_result, f"OTP response should not expose devOtp for {phone}")

    verify_result = api.post("/auth/verify-otp", data={"phone": phone, "otp": DEV_OTP_BYPASS})
    check(verify_result.get("success") is True, f"OTP verify should succeed for {phone}")
    check(verify_result.get("role") == expected_role, f"Expected role {expected_role} for {phone}")
    token = verify_result.get("token")
    check(isinstance(token, str) and token, f"Token should exist for {phone}")

    authed = api.with_token(token)
    profile = authed.get("/me")
    check(profile.get("role") == expected_role, f"/me role mismatch for {phone}")
    return Session(token=token, role=expected_role, profile=profile)


def receive_next_order_stream_event(
    *,
    base_url: str,
    token: str,
    restaurant_id: int,
    trigger: Callable[[], Any],
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    ready = threading.Event()
    received = threading.Event()
    errors: list[Exception] = []
    payload: dict[str, Any] = {}
    response_holder: dict[str, Any] = {}

    def reader() -> None:
        event_name = "message"
        stream_url = f"{base_url}/orders/stream?{urllib.parse.urlencode({'token': token, 'restaurantId': restaurant_id})}"
        request = urllib.request.Request(stream_url, headers={"Accept": "text/event-stream"})
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds + 5) as response:
                response_holder["response"] = response
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()
                    if not line:
                        continue
                    if line.startswith(":"):
                        continue
                    if line.startswith("event:"):
                        event_name = line.split(":", 1)[1].strip()
                        continue
                    if not line.startswith("data:"):
                        continue

                    data = json.loads(line.split(":", 1)[1].strip())
                    if event_name == "ready":
                        ready.set()
                        continue
                    if event_name == "order_update":
                        payload.update(data)
                        received.set()
                        return
        except Exception as exc:
            errors.append(exc)
            ready.set()

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()

    if not ready.wait(timeout_seconds):
        raise SmokeTestError("Order stream did not become ready in time")
    if errors:
        raise SmokeTestError(f"Order stream failed before receiving events: {errors[0]}")

    trigger()

    if not received.wait(timeout_seconds):
        raise SmokeTestError("Order stream did not receive an update event in time")

    response = response_holder.get("response")
    if response is not None:
        response.close()

    thread.join(timeout=1)

    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test the Aloegy backend API.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Backend base URL")
    parser.add_argument(
        "--require-demo-session",
        action="store_true",
        help="Fail the smoke test if /demo/livekit-session cannot reach LiveKit",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("BACKEND_API_KEY", "mock_secret_key"),
        help="Agent API key for protected backend contract routes",
    )
    args = parser.parse_args()

    api = ApiClient(args.base_url)

    try:
        health = api.get("/health")
        check(health.get("status") == "ok", "Health endpoint should return ok")
        log_pass("health endpoint is healthy")

        contact_result = api.post(
            "/contact",
            data={
                "restaurantName": "Smoke Contact Restaurant",
                "phone": "+201166778899",
                "message": "smoke test lead",
            },
        )
        check(contact_result is True, "Contact form should return true")
        log_pass("contact form works")

        for index in range(2):
            send_result = api.post("/auth/send-otp", data={"phone": ADMIN_PHONE})
            check(send_result.get("success") is True, "Admin OTP send should succeed")
            check("devOtp" not in send_result, "Admin OTP send should not expose devOtp")
            log_pass(f"login attempt send OTP #{index + 1} works without exposing OTP")

        try:
            api.post("/auth/verify-otp", data={"phone": ADMIN_PHONE, "otp": "000000"})
            raise SmokeTestError("Invalid OTP should be rejected")
        except ApiError as exc:
            check(exc.status_code == 400, "Invalid OTP should return 400")
            detail = exc.body.get("detail") if isinstance(exc.body, dict) else str(exc.body)
            check(detail == "invalid otp", "Invalid OTP detail should be stable")
        log_pass("invalid OTP attempt is rejected")

        admin_session = login_with_bypass(api, ADMIN_PHONE, "admin")
        log_pass("admin login works with bypass OTP")

        sales_session = login_with_bypass(api, SALES_PHONE, "sales")
        log_pass("sales login works with bypass OTP")

        owner_session = login_with_bypass(api, OWNER_PHONE, "owner")
        owner_restaurant_id = owner_session.profile.get("restaurantId")
        check(isinstance(owner_restaurant_id, int), "Owner should be scoped to a restaurant")
        log_pass("owner login works and returns restaurant scope")

        new_owner_phone = unique_phone("13")
        try:
            api.post("/auth/send-otp", data={"phone": new_owner_phone})
            raise SmokeTestError("Unknown owner phone should be rejected")
        except ApiError as exc:
            check(exc.status_code == 403, "Unknown owner phone should return 403")
        log_pass("unknown owner phone is rejected")

        owner_api = api.with_token(owner_session.token)
        stats = owner_api.get("/stats")
        calls = owner_api.get("/calls")
        users = owner_api.get("/users")
        orders = owner_api.get("/orders")
        files = owner_api.get("/files")
        settings = owner_api.get("/settings")
        menu_items = owner_api.get("/menu-items")
        employees = owner_api.get("/employees")
        issues = owner_api.get("/issues")
        analytics = owner_api.get("/analytics")

        check(isinstance(stats, dict) and "totalCalls" in stats, "Stats payload is invalid")
        check(isinstance(calls, list), "Calls payload should be a list")
        check(isinstance(users, list), "Users payload should be a list")
        check(isinstance(orders, list) and orders, "Orders payload should include seeded orders")
        check(isinstance(files, list) and files, "Files payload should include a seeded file")
        check(isinstance(settings, dict) and "restaurant" in settings and "agent" in settings, "Settings payload is invalid")
        check(isinstance(menu_items, list) and menu_items, "Menu items payload should include seeded items")
        check(isinstance(employees, list), "Employees payload should be a list")
        check(isinstance(issues, list), "Issues payload should be a list")
        check(isinstance(analytics, dict) and "summary" in analytics, "Analytics payload is invalid")
        log_pass("owner dashboard fetch endpoints work")

        first_order = orders[0]
        original_order_status = first_order["status"]
        updated_order = owner_api.patch(
            f"/orders/{first_order['id']}",
            data={"status": "completed", "driverPhone": "+201122233344"},
        )
        check(updated_order["status"] == "delivered", "Order update should canonicalize completed to delivered")
        owner_api.patch(
            f"/orders/{first_order['id']}",
            data={"status": original_order_status, "driverPhone": None},
        )
        log_pass("order update works")

        original_settings = json.loads(json.dumps(settings))
        updated_settings_payload = json.loads(json.dumps(settings))
        updated_settings_payload["restaurant"]["name"] = f"{settings['restaurant']['name']} Smoke"
        updated_settings_payload["agent"]["name"] = f"{settings['agent']['name']} Smoke"
        updated_settings = owner_api.put("/settings", data=updated_settings_payload)
        check(updated_settings["restaurant"]["name"].endswith("Smoke"), "Settings update should persist restaurant name")
        owner_api.put("/settings", data=original_settings)
        log_pass("settings update works")

        created_item = owner_api.post(
            "/menu-items",
            data={
                "name": "Smoke Meal",
                "category": "وجبات",
                "ingredients": "rice, sauce",
                "smallPrice": "11",
                "mediumPrice": "22",
                "largePrice": "33",
                "available": True,
            },
        )
        check(created_item["name"] == "Smoke Meal", "Menu item creation should persist name")
        updated_item = owner_api.put(
            f"/menu-items/{created_item['id']}",
            data={
                "name": "Smoke Meal Updated",
                "category": "وجبات",
                "ingredients": "rice, sauce, herbs",
                "smallPrice": "15",
                "mediumPrice": "25",
                "largePrice": "35",
                "available": False,
            },
        )
        check(updated_item["name"] == "Smoke Meal Updated", "Menu item update should persist")
        owner_api.delete(f"/menu-items/{created_item['id']}")
        log_pass("menu item CRUD works")

        employee_phone = unique_phone("14")
        created_employee = owner_api.post("/employees", data={"name": "Smoke Employee", "phone": employee_phone})
        check(created_employee["phone"] == employee_phone, "Employee creation should persist phone")
        owner_api.delete(f"/employees/{created_employee['id']}")
        log_pass("employee CRUD works")

        if issues:
            first_issue = issues[0]
            target_status = "resolved" if first_issue["status"] != "resolved" else "new"
            updated_issue = owner_api.patch(f"/issues/{first_issue['id']}", data={"status": target_status})
            check(updated_issue["status"] == target_status, "Issue status update should persist")
            owner_api.patch(f"/issues/{first_issue['id']}", data={"status": first_issue["status"]})
            log_pass("issue update works")
        else:
            log_warn("issue update skipped because there are no seeded issues")

        preview = owner_api.get(f"/files/{files[0]['id']}/preview")
        check(isinstance(preview.get("url"), str) and preview["url"], "File preview URL should exist")
        downloaded_file = owner_api.get(f"/files/{files[0]['id']}/download", raw=True)
        check(isinstance(downloaded_file, bytes) and len(downloaded_file) > 0, "File download should return bytes")
        log_pass("file preview and download work")

        sales_api = api.with_token(sales_session.token)
        sales_requests = sales_api.get("/sales/requests")
        demo_sessions = sales_api.get("/sales/demo-sessions")
        check(isinstance(sales_requests, list), "Sales requests should be a list")
        check(isinstance(demo_sessions, list), "Demo sessions should be a list")

        created_sales_request = sales_api.post(
            "/sales/requests",
            data={
                "restaurantName": "Smoke Sales Restaurant",
                "ownerName": "Smoke Owner",
                "ownerPhone": unique_phone("15"),
                "location": "Nasr City",
            },
        )
        check(created_sales_request["restaurantName"] == "Smoke Sales Restaurant", "Sales request creation should persist")

        created_demo_session = sales_api.post(
            "/sales/demo-sessions",
            data={
                "restaurantName": "Smoke Demo Restaurant",
                "phoneNumber": unique_phone("16"),
            },
        )
        check(created_demo_session["status"] == "scheduled", "Demo session creation should default to scheduled")
        log_pass("sales dashboard create endpoints work")

        admin_api = api.with_token(admin_session.token)
        admin_restaurants = admin_api.get("/admin/restaurants")
        admin_sales_team = admin_api.get("/admin/sales-team")
        admin_sales_requests = admin_api.get("/admin/sales-requests")
        admin_overview = admin_api.get("/admin/overview")
        check(isinstance(admin_restaurants, list) and admin_restaurants, "Admin restaurants should be available")
        check(isinstance(admin_sales_team, list) and admin_sales_team, "Admin sales team should be available")
        check(isinstance(admin_sales_requests, list) and admin_sales_requests, "Admin sales requests should be available")
        check(isinstance(admin_overview, dict) and "liveCalls" in admin_overview, "Admin overview is invalid")

        created_sales_member = admin_api.post(
            "/admin/sales-team",
            data={"name": "Smoke Sales Member", "phone": unique_phone("17")},
        )
        check(created_sales_member["name"] == "Smoke Sales Member", "Sales team member creation should persist")
        admin_api.delete(f"/admin/sales-team/{created_sales_member['id']}")

        approved_sales_request = admin_api.patch(
            f"/admin/sales-requests/{created_sales_request['id']}",
            data={"status": "approved"},
        )
        check(approved_sales_request["status"] == "approved", "Admin should be able to approve sales requests")

        created_restaurant = admin_api.post(
            "/admin/restaurants",
            data={
                "name": "Smoke Admin Restaurant",
                "ownerName": "Smoke Admin Owner",
                "ownerPhone": unique_phone("18"),
                "location": "Heliopolis",
                "plan": "أساسي",
                "assignedPhone": "+20220000000",
            },
        )
        check(created_restaurant["restaurantName"] == "Smoke Admin Restaurant", "Admin restaurant creation should persist")
        log_pass("admin dashboard endpoints work")

        agent_headers = {
            "X-API-Key": args.api_key,
            "X-Restaurant-ID": DEFAULT_DEMO_RESTAURANT_ID,
        }
        agent_config = api.get("/restaurant/config", headers=agent_headers)
        check(agent_config.get("id") == DEFAULT_DEMO_RESTAURANT_ID, "Agent config should resolve the demo restaurant")

        agent_order_headers = {
            **agent_headers,
            "Idempotency-Key": f"smoke-order-{int(time.time() * 1000)}",
        }
        agent_order_payload = {
            "call_id": "smoke-call-order",
            "type": "delivery",
            "customer_name": "Smoke Caller",
            "customer_phone": unique_phone("19"),
            "order_items": [
                {"name": "Smoke Pizza", "qty": 2, "price": 55},
                {"name": "Cola", "qty": 1, "price": 20},
            ],
            "special_requests": "extra cheese",
            "delivery_address": "Dokki",
            "delivery_zone": "Giza",
            "delivery_landmark": "Near metro",
            "channel": "voice_agent",
        }
        first_order_response: dict[str, Any] = {}

        def trigger_agent_order() -> None:
            first_order_response.update(api.post("/orders", data=agent_order_payload, headers=agent_order_headers))

        created_stream_event = receive_next_order_stream_event(
            base_url=args.base_url,
            token=owner_session.token,
            restaurant_id=owner_restaurant_id,
            trigger=trigger_agent_order,
        )
        check(created_stream_event.get("action") == "created", "Order stream should emit a created event")
        check(created_stream_event.get("order", {}).get("id") == first_order_response.get("order_id"), "Order stream created event should include the new order id")

        second_order_response = api.post("/orders", data=agent_order_payload, headers=agent_order_headers)
        check(first_order_response["order_id"] == second_order_response["order_id"], "Agent order idempotency should work")

        updated_stream_event = receive_next_order_stream_event(
            base_url=args.base_url,
            token=owner_session.token,
            restaurant_id=owner_restaurant_id,
            trigger=lambda: owner_api.patch(
                f"/orders/{first_order_response['order_id']}",
                data={"status": "preparing", "driverPhone": None},
            ),
        )
        check(updated_stream_event.get("action") == "updated", "Order stream should emit an updated event")
        check(updated_stream_event.get("order", {}).get("id") == first_order_response["order_id"], "Order stream update event should target the changed order")
        check(updated_stream_event.get("order", {}).get("status") == "preparing", "Order stream update event should include the latest status")
        log_pass("order stream emits live create and update events")

        reservation_response = api.post(
            "/reservations",
            data={
                "call_id": "smoke-call-reservation",
                "customer_name": "Smoke Guest",
                "customer_phone": unique_phone("20"),
                "reservation_time": "2026-03-24T20:00:00+02:00",
                "guests_count": 4,
                "branch": "Dokki",
                "notes": "window seat",
                "channel": "voice_agent",
            },
            headers={
                **agent_headers,
                "Idempotency-Key": f"smoke-reservation-{int(time.time() * 1000)}",
            },
        )
        check(isinstance(reservation_response.get("reservation_id"), str), "Agent reservation should create an id")

        complaint_response = api.post(
            "/complaints",
            data={
                "call_id": "smoke-call-complaint",
                "customer_name": "Smoke Customer",
                "customer_phone": unique_phone("21"),
                "complaint_text": "order arrived cold",
                "complaint_type": "delivery",
                "channel": "voice_agent",
            },
            headers={
                **agent_headers,
                "Idempotency-Key": f"smoke-complaint-{int(time.time() * 1000)}",
            },
        )
        check(complaint_response.get("status") == "new", "Agent complaint should default to new")
        log_pass("agent contract endpoints work")

        try:
            demo_session_response = api.post(
                "/demo/livekit-session",
                data={
                    "restaurantId": DEFAULT_DEMO_RESTAURANT_ID,
                    "participantName": "Smoke Test Visitor",
                },
            )
            check(demo_session_response.get("ok") is True, "Demo session should return ok=true")
            check(isinstance(demo_session_response.get("token"), str) and demo_session_response["token"], "Demo session should include a token")
            log_pass("live demo session endpoint works")
        except ApiError as exc:
            if exc.status_code == 503 and not args.require_demo_session:
                detail = exc.body.get("detail") if isinstance(exc.body, dict) else str(exc.body)
                log_warn(f"live demo session is unavailable in this environment: {detail}")
            else:
                raise

        print("\nSmoke test completed successfully.")
        return 0
    except (SmokeTestError, ApiError) as exc:
        print(f"\nSmoke test failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

