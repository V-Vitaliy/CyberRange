import pytest
from fastapi.testclient import TestClient
from app.main import app

# Create a single test client for the test suite
client = TestClient(app)

class TestVulnerableEndpoints:
    """
    Test suite for intentionally vulnerable endpoints (Red Team vectors).
    These tests ensure that the vulnerabilities are exposed exactly as designed
    for educational purposes.
    """

    def test_chat_history_sqli_vulnerability(self):
        """
        Verifies that the /api/chat/history endpoint is vulnerable to SQL Injection.
        It asserts that injecting a raw SQL payload breaks the query and returns
        a raw database error message (which students use to discover the flaw).
        """
        # 1. Arrange: Prepare the malicious payload
        # This specific payload attempts to break the string concatenation in the raw SQL query
        malicious_payload = "') OR 1=1 --"
        test_session_id = "123e4567-e89b-12d3-a456-426614174000"
        
        # 2. Act: Send the GET request to the vulnerable endpoint
        response = client.get(
            "/api/chat/history",
            params={"q": malicious_payload, "session_id": test_session_id}
        )
        
        # 3. Assert: Check the HTTP status code
        # The API should catch the database crash and return a 400 Bad Request
        assert response.status_code == 400, f"Expected HTTP 400, got {response.status_code}"
        
        response_data = response.json()
        
        # 4. Assert: Check for database error leakage
        # The core of the vulnerability is leaking the raw PostgreSQL error to the client
        assert "detail" in response_data, "Response payload is missing the 'detail' key"
        
        error_message = response_data["detail"].lower()
        is_vulnerable = "syntax error" in error_message or "error" in error_message
        
        assert is_vulnerable, "Endpoint is NOT vulnerable! SQL Injection failed to leak DB errors."


class TestSystemHealth:
    """
    Test suite for basic system health and availability.
    """

    def test_health_check(self):
        """
        Verifies that the /api/health endpoint returns a 200 OK status
        and the expected JSON payload indicating the system is online.
        """
        response = client.get("/api/health")
        
        assert response.status_code == 200, f"Expected HTTP 200, got {response.status_code}"
        
        data = response.json()
        assert data.get("status") == "online", "System status is not reported as 'online'"
        assert "service" in data, "Service name is missing from the health check response"