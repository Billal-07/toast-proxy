from fastapi import FastAPI, HTTPException
from datetime import datetime
import httpx
import os

app = FastAPI(title="Toast POS API", version="1.0.0")

TOAST_BASE_URL = "https://ws-api.toasttab.com"

# ── Put your credentials here ──────────────────────────────
CLIENT_ID =  os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
USER_ACCESS_TYPE = "TOAST_MACHINE_CLIENT"
RESTAURANT_GUID  = os.getenv("RESTAURANT_GUID")
TOAST_MANAGEMENT_GROUP_EXTERNAL_ID = os.getenv("TOAST_MANAGEMENT_GROUP_EXTERNAL_ID")
# ───────────────────────────────────────────────────────────


async def get_auth_token() -> str:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{TOAST_BASE_URL}/authentication/v1/authentication/login",
            json={
                "clientId": CLIENT_ID,
                "clientSecret": CLIENT_SECRET,
                "userAccessType": USER_ACCESS_TYPE
            },
            headers={"Content-Type": "application/json"}
        )
        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Auth failed: {response.text}"
            )
        return response.json()["token"]["accessToken"]


@app.get("/health", tags=["General"])
async def health():
    return {"status": "ok", "message": "Toast Proxy is running"}


@app.get("/debug", tags=["Debug"])
async def debug():
    """Shows exactly what credentials are being used."""
    return {
        "CLIENT_ID": CLIENT_ID,
        "CLIENT_SECRET": f"{CLIENT_SECRET[:4]}****",  # hides most of secret
        "USER_ACCESS_TYPE": USER_ACCESS_TYPE,
        "TOAST_BASE_URL": TOAST_BASE_URL,
        "RESTAURANT_GUID": RESTAURANT_GUID,
        "TOAST_MANAGEMENT_GROUP_EXTERNAL_ID": TOAST_MANAGEMENT_GROUP_EXTERNAL_ID
    }

@app.get("/login", tags=["Auth"])
async def login():
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{TOAST_BASE_URL}/authentication/v1/authentication/login",
            json={
                "clientId": CLIENT_ID,
                "clientSecret": CLIENT_SECRET,
                "userAccessType": USER_ACCESS_TYPE
            },
            headers={"Content-Type": "application/json"}
        )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    return {"success": True, "status_code": response.status_code, "response": response.json()}


@app.get("/metrics", tags=["Metrics"])
async def get_metrics(
    startBusinessDate: str = "20260401",
    endBusinessDate: str = "20260407"
):
    """
    Workflow:
    1. Login → get access token
    2. Fetch all restaurant IDs from /era/v1/restaurants-information
    3. POST to /era/v1/metrics → get job ID
    4. GET /era/v1/metrics/{job_id} → fetch actual metrics result
    5. Return final metrics data
    """
    try:
        # ── STEP 1: LOGIN ──────────────────────────────────────────
        async with httpx.AsyncClient() as client:
            auth_response = await client.post(
                f"{TOAST_BASE_URL}/authentication/v1/authentication/login",
                json={
                    "clientId": CLIENT_ID,
                    "clientSecret": CLIENT_SECRET,
                    "userAccessType": USER_ACCESS_TYPE
                },
                headers={"Content-Type": "application/json"}
            )

        if auth_response.status_code != 200:
            return {
                "step": "login",
                "error": "Login failed",
                "status_code": auth_response.status_code,
                "detail": auth_response.text
            }

        try:
            access_token = auth_response.json()["token"]["accessToken"]
        except KeyError as e:
            return {
                "step": "token_extraction",
                "error": f"Could not extract token — missing key: {str(e)}",
                "auth_response": auth_response.json()
            }

        common_headers = {
            "Authorization": f"Bearer {access_token}",
            "Toast-Management-Group-External-ID": TOAST_MANAGEMENT_GROUP_EXTERNAL_ID,
            "Content-Type": "application/json"
        }

        # ── STEP 2: FETCH ALL RESTAURANT IDs ──────────────────────
        async with httpx.AsyncClient() as client:
            restaurants_response = await client.get(
                f"{TOAST_BASE_URL}/era/v1/restaurants-information",
                headers=common_headers
            )

        if restaurants_response.status_code != 200:
            return {
                "step": "restaurants_information",
                "error": "Failed to fetch restaurants",
                "status_code": restaurants_response.status_code,
                "detail": restaurants_response.text
            }

        try:
            restaurants_data = restaurants_response.json()
        except Exception as e:
            return {
                "step": "restaurants_information",
                "error": "Failed to parse restaurants response",
                "detail": str(e),
                "raw": restaurants_response.text
            }

        # Extract only active, non-archived restaurant IDs
        restaurant_ids = [
            r["restaurantGuid"]
            for r in restaurants_data
            if r.get("active") and not r.get("archived")
        ]

        if not restaurant_ids:
            return {
                "step": "restaurant_ids_extraction",
                "error": "No active restaurants found",
                "raw_restaurants": restaurants_data
            }

        # ── STEP 3: POST METRICS → GET JOB ID ─────────────────────
        async with httpx.AsyncClient() as client:
            metrics_response = await client.post(
                f"{TOAST_BASE_URL}/era/v1/metrics",
                headers=common_headers,
                json={
                    "startBusinessDate": startBusinessDate,
                    "endBusinessDate": endBusinessDate,
                    "restaurantIds": restaurant_ids,
                    "excludedRestaurantIds": []
                }
            )

        if metrics_response.status_code != 200:
            return {
                "step": "metrics_post",
                "error": "Failed to post metrics request",
                "status_code": metrics_response.status_code,
                "detail": metrics_response.text
            }

        try:
            job_id = metrics_response.json()
        except Exception as e:
            return {
                "step": "metrics_post",
                "error": "Failed to parse metrics job ID",
                "detail": str(e),
                "raw": metrics_response.text
            }

        if not job_id:
            return {
                "step": "metrics_post",
                "error": "No job ID returned from metrics endpoint",
                "raw": metrics_response.text
            }

        # ── STEP 4: POLL METRICS RESULT WITH JOB ID ───────────────
        import asyncio

        max_retries = 10       # try up to 10 times
        retry_delay = 3        # wait 3 seconds between each retry
        metrics_result = None

        for attempt in range(1, max_retries + 1):
            await asyncio.sleep(retry_delay)

            async with httpx.AsyncClient() as client:
                result_response = await client.get(
                    f"{TOAST_BASE_URL}/era/v1/metrics/{job_id}/",
                    headers=common_headers
                )

            # Log each attempt for debugging
            print(f"Attempt {attempt}: status={result_response.status_code}, body={result_response.text[:200]}")

            if result_response.status_code == 200:
                try:
                    metrics_result = result_response.json()
                except Exception as e:
                    return {
                        "step": "metrics_result_parse",
                        "error": "Failed to parse metrics result",
                        "detail": str(e),
                        "raw": result_response.text
                    }

                # Check if result is still processing
                # Toast may return a status field like "PENDING" or "PROCESSING"
                if isinstance(metrics_result, dict):
                    status = metrics_result.get("status", "").upper()
                    if status in ["PENDING", "PROCESSING", "IN_PROGRESS"]:
                        print(f"Attempt {attempt}: Still processing — status={status}")
                        continue  # keep polling

                # Got a real result — break out
                break

            elif result_response.status_code == 202:
                # 202 Accepted means still processing
                print(f"Attempt {attempt}: 202 Accepted — still processing")
                continue

            else:
                return {
                    "step": "metrics_result_fetch",
                    "error": "Failed to fetch metrics result",
                    "attempt": attempt,
                    "job_id": job_id,
                    "status_code": result_response.status_code,
                    "detail": result_response.text
                }

        if metrics_result is None:
            return {
                "step": "metrics_result_fetch",
                "error": f"Metrics result not ready after {max_retries} attempts ({max_retries * retry_delay}s)",
                "job_id": job_id,
                "suggestion": "Try calling /metrics/result/{job_id} manually after a few seconds"
            }

        # ── RETURN FINAL RESPONSE ──────────────────────────────────
        return {
            "success": True,
            "startBusinessDate": startBusinessDate,
            "endBusinessDate": endBusinessDate,
            "total_restaurants": len(restaurant_ids),
            "restaurant_ids": restaurant_ids,
            "job_id": job_id,
            "metrics": metrics_result
        }

    except httpx.ConnectError as e:
        return {"step": "network", "error": "Connection error", "detail": str(e)}
    except httpx.TimeoutException as e:
        return {"step": "network", "error": "Request timed out", "detail": str(e)}
    except Exception as e:
        return {"step": "unknown", "error": "Unexpected error", "error_type": type(e).__name__, "detail": str(e)}


# ── BONUS: Fetch metrics result manually by job ID ─────────────
@app.get("/metrics/result/{job_id}", tags=["Metrics"])
async def get_metrics_result(job_id: str):
    """
    Manually fetch metrics result using a job ID.
    Use this if /metrics times out before the result is ready.
    """
    try:
        access_token = await get_auth_token()

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{TOAST_BASE_URL}/era/v1/metrics/{job_id}/",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Toast-Management-Group-External-ID": TOAST_MANAGEMENT_GROUP_EXTERNAL_ID,
                    "Content-Type": "application/json"
                }
            )

        return {
            "success": response.status_code == 200,
            "job_id": job_id,
            "status_code": response.status_code,
            "response": response.json() if response.status_code == 200 else None,
            "raw": response.text if response.status_code != 200 else None
        }

    except Exception as e:
        return {"step": "unknown", "error": str(e), "error_type": type(e).__name__}



@app.get("/restaurants-information", tags=["Restaurants"])
async def get_restaurants_information():
    """
    Logs in first, then uses the access token to fetch
    restaurant information from /era/v1/restaurants-information
    """
    # Step 1 — Login and get token
    async with httpx.AsyncClient() as client:
        auth_response = await client.post(
            f"{TOAST_BASE_URL}/authentication/v1/authentication/login",
            json={
                "clientId": CLIENT_ID,
                "clientSecret": CLIENT_SECRET,
                "userAccessType": USER_ACCESS_TYPE
            },
            headers={"Content-Type": "application/json"}
        )

    if auth_response.status_code != 200:
        raise HTTPException(
            status_code=auth_response.status_code,
            detail=f"Login failed: {auth_response.text}"
        )

    auth_data = auth_response.json()
    access_token = auth_data["token"]["accessToken"]

    # Step 2 — Use token to fetch restaurant information
    async with httpx.AsyncClient() as client:
        info_response = await client.get(
            f"{TOAST_BASE_URL}/era/v1/restaurants-information",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Toast-Management-Group-External-ID": TOAST_MANAGEMENT_GROUP_EXTERNAL_ID
            }
        )

    if info_response.status_code != 200:
        raise HTTPException(
            status_code=info_response.status_code,
            detail=f"Failed to fetch restaurant info: {info_response.text}"
        )

    return {
        "success": True,
        "status_code": info_response.status_code,
        "access_token_used": f"{access_token[:20]}****",  # partially hidden for safety
        "response": info_response.json()
    }


@app.get("/restaurants", tags=["Restaurants"])
async def get_restaurants():
    token = await get_auth_token()
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{TOAST_BASE_URL}/restaurants/v1/groups",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    return {"success": True, "status_code": response.status_code, "response": response.json()}


@app.get("/restaurants/details", tags=["Restaurants"])
async def get_restaurant_details():
    token = await get_auth_token()
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{TOAST_BASE_URL}/restaurants/v1/restaurants/{RESTAURANT_GUID}",
            headers={
                "Authorization": f"Bearer {token}",
                "Toast-Restaurant-External-ID": RESTAURANT_GUID,
                "Content-Type": "application/json"
            }
        )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    return {"success": True, "status_code": response.status_code, "response": response.json()}


@app.get("/orders/today", tags=["Orders"])
async def get_orders_today():
    token = await get_auth_token()
    today = datetime.utcnow().strftime("%Y%m%d")
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{TOAST_BASE_URL}/orders/v2/ordersBulk",
            params={"businessDate": today},
            headers={
                "Authorization": f"Bearer {token}",
                "Toast-Restaurant-External-ID": RESTAURANT_GUID,
                "Content-Type": "application/json"
            }
        )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    data = response.json()
    return {"success": True, "status_code": response.status_code, "business_date": today, "total_orders": len(data) if isinstance(data, list) else None, "response": data}


@app.get("/orders/range", tags=["Orders"])
async def get_orders_by_range(
    startDate: str = "2026-04-01T00:00:00.000+0000",
    endDate: str   = "2026-04-30T23:59:59.000+0000"
):
    token = await get_auth_token()
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{TOAST_BASE_URL}/orders/v2/ordersBulk",
            params={"startDate": startDate, "endDate": endDate},
            headers={
                "Authorization": f"Bearer {token}",
                "Toast-Restaurant-External-ID": RESTAURANT_GUID,
                "Content-Type": "application/json"
            }
        )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    data = response.json()
    return {"success": True, "status_code": response.status_code, "startDate": startDate, "endDate": endDate, "total_orders": len(data) if isinstance(data, list) else None, "response": data}


@app.get("/orders/date/{business_date}", tags=["Orders"])
async def get_orders_by_date(business_date: str):
    token = await get_auth_token()
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{TOAST_BASE_URL}/orders/v2/ordersBulk",
            params={"businessDate": business_date},
            headers={
                "Authorization": f"Bearer {token}",
                "Toast-Restaurant-External-ID": RESTAURANT_GUID,
                "Content-Type": "application/json"
            }
        )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    data = response.json()
    return {"success": True, "status_code": response.status_code, "business_date": business_date, "total_orders": len(data) if isinstance(data, list) else None, "response": data}


@app.get("/orders/{order_guid}", tags=["Orders"])
async def get_order_by_guid(order_guid: str):
    token = await get_auth_token()
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{TOAST_BASE_URL}/orders/v2/orders/{order_guid}",
            headers={
                "Authorization": f"Bearer {token}",
                "Toast-Restaurant-External-ID": RESTAURANT_GUID,
                "Content-Type": "application/json"
            }
        )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    return {"success": True, "status_code": response.status_code, "order_guid": order_guid, "response": response.json()}


@app.get("/payments", tags=["Payments"])
async def get_payments(paidBusinessDate: str = None):
    token = await get_auth_token()
    params = {}
    if paidBusinessDate:
        params["paidBusinessDate"] = paidBusinessDate
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{TOAST_BASE_URL}/orders/v2/payments",
            params=params,
            headers={
                "Authorization": f"Bearer {token}",
                "Toast-Restaurant-External-ID": RESTAURANT_GUID,
                "Content-Type": "application/json"
            }
        )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    return {"success": True, "status_code": response.status_code, "response": response.json()}

