from fastapi import FastAPI, HTTPException
from datetime import datetime, timedelta
from pydantic import BaseModel
from typing import List
import httpx
import asyncio
import os
import json

app = FastAPI(title="Toast POS API", version="1.0.0")

TOAST_BASE_URL = "https://ws-api.toasttab.com"

# ── Credentials from environment variables ─────────────────
CLIENT_ID                           = os.getenv("CLIENT_ID")
CLIENT_SECRET                       = os.getenv("CLIENT_SECRET")
USER_ACCESS_TYPE                    = "TOAST_MACHINE_CLIENT"
RESTAURANT_GUID                     = os.getenv("RESTAURANT_GUID")
TOAST_MANAGEMENT_GROUP_EXTERNAL_ID  = os.getenv("TOAST_MANAGEMENT_GROUP_EXTERNAL_ID")

# ── Restaurant IDs from env (JSON array string) ────────────
# In Vercel env, set it as:
# RESTAURANT_IDS=["id-1","id-2","id-3"]
_raw_restaurant_ids = os.getenv("RESTAURANT_IDS", "[]")
try:
    RESTAURANT_IDS: List[str] = json.loads(_raw_restaurant_ids)
except Exception:
    RESTAURANT_IDS: List[str] = []
# ───────────────────────────────────────────────────────────


# ── TOKEN CACHE ────────────────────────────────────────────
token_cache = {
    "access_token": None,
    "expires_at": None
}
# ───────────────────────────────────────────────────────────


# ── PYDANTIC MODELS ────────────────────────────────────────
class MetricsCustomRequest(BaseModel):
    startBusinessDate: str = "20260401"
    endBusinessDate: str   = "20260407"
    restaurantIds: List[str]
    excludedRestaurantIds: List[str] = []
# ───────────────────────────────────────────────────────────


# ── HELPERS ────────────────────────────────────────────────

async def get_auth_token() -> str:
    """
    Returns cached token if still valid.
    Fetches a new one only if expired or missing.
    """
    now = datetime.utcnow()

    if (
        token_cache["access_token"]
        and token_cache["expires_at"]
        and now < token_cache["expires_at"]
    ):
        return token_cache["access_token"]

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
            detail=f"Login failed: {response.text}"
        )

    data         = response.json()
    access_token = data["token"]["accessToken"]
    expires_in   = data["token"].get("expiresIn", 86400)

    token_cache["access_token"] = access_token
    token_cache["expires_at"]   = now + timedelta(seconds=expires_in - 300)

    return access_token


async def poll_metrics_result(job_id: str, headers: dict):
    """
    Polls /era/v1/metrics/{job_id} until result is ready.
    """
    max_retries = 10
    retry_delay = 3

    for attempt in range(1, max_retries + 1):
        await asyncio.sleep(retry_delay)

        async with httpx.AsyncClient() as client:
            result_response = await client.get(
                f"{TOAST_BASE_URL}/era/v1/metrics/{job_id}/",
                headers=headers
            )

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

            if isinstance(metrics_result, dict):
                status = metrics_result.get("status", "").upper()
                if status in ["PENDING", "PROCESSING", "IN_PROGRESS"]:
                    print(f"Attempt {attempt}: Still processing — status={status}")
                    continue

            return {"success": True, "result": metrics_result}

        elif result_response.status_code == 202:
            print(f"Attempt {attempt}: 202 Accepted — still processing")
            continue

        elif result_response.status_code == 429:
            return {
                "step": "metrics_result_fetch",
                "error": "Rate limited while polling",
                "job_id": job_id,
                "suggestion": f"Use /metrics/result/{job_id} after 60 seconds"
            }

        else:
            return {
                "step": "metrics_result_fetch",
                "error": "Failed to fetch metrics result",
                "attempt": attempt,
                "job_id": job_id,
                "status_code": result_response.status_code,
                "detail": result_response.text
            }

    return {
        "step": "metrics_result_fetch",
        "error": f"Not ready after {max_retries} attempts ({max_retries * retry_delay}s)",
        "job_id": job_id,
        "suggestion": f"Use /metrics/result/{job_id} to fetch manually"
    }


# ═══════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════

# ── GENERAL ───────────────────────────────────────────────

@app.get("/health", tags=["General"])
async def health():
    now = datetime.utcnow()
    return {
        "status": "ok",
        "message": "Toast Proxy is running",
        "token_cache": {
            "cached": token_cache["access_token"] is not None,
            "valid": (
                token_cache["expires_at"] is not None
                and now < token_cache["expires_at"]
            ),
            "expires_at": str(token_cache["expires_at"])
        },
        "restaurant_ids_from_env": {
            "count": len(RESTAURANT_IDS),
            "ids": RESTAURANT_IDS
        }
    }


@app.get("/cache/clear", tags=["General"])
async def clear_cache():
    """Force clear cached token."""
    token_cache["access_token"] = None
    token_cache["expires_at"]   = None
    return {"success": True, "message": "Token cache cleared — next request will login fresh"}


@app.get("/debug", tags=["General"])
async def debug():
    """Shows credentials being used (secret is masked)."""
    return {
        "CLIENT_ID": CLIENT_ID,
        "CLIENT_SECRET": f"{CLIENT_SECRET[:4]}****" if CLIENT_SECRET else None,
        "USER_ACCESS_TYPE": USER_ACCESS_TYPE,
        "TOAST_BASE_URL": TOAST_BASE_URL,
        "RESTAURANT_GUID": RESTAURANT_GUID,
        "TOAST_MANAGEMENT_GROUP_EXTERNAL_ID": TOAST_MANAGEMENT_GROUP_EXTERNAL_ID,
        "RESTAURANT_IDS": RESTAURANT_IDS,
        "RESTAURANT_IDS_count": len(RESTAURANT_IDS)
    }


# ── AUTH ───────────────────────────────────────────────────

@app.get("/login", tags=["Auth"])
async def login():
    """Login and return full auth response. Uses cached token if valid."""
    now = datetime.utcnow()

    if (
        token_cache["access_token"]
        and token_cache["expires_at"]
        and now < token_cache["expires_at"]
    ):
        return {
            "success": True,
            "source": "cache",
            "token_expires_at": str(token_cache["expires_at"]),
            "access_token_preview": f"{token_cache['access_token'][:20]}****"
        }

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

    data         = response.json()
    access_token = data["token"]["accessToken"]
    expires_in   = data["token"].get("expiresIn", 86400)

    token_cache["access_token"] = access_token
    token_cache["expires_at"]   = now + timedelta(seconds=expires_in - 300)

    return {
        "success": True,
        "source": "fresh",
        "status_code": response.status_code,
        "token_expires_at": str(token_cache["expires_at"]),
        "response": data
    }


# ── RESTAURANTS ────────────────────────────────────────────

@app.get("/restaurants-information", tags=["Restaurants"])
async def get_restaurants_information():
    """Login (cached) → fetch all restaurant information."""
    try:
        token = await get_auth_token()

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{TOAST_BASE_URL}/era/v1/restaurants-information",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Toast-Management-Group-External-ID": TOAST_MANAGEMENT_GROUP_EXTERNAL_ID,
                    "Content-Type": "application/json"
                }
            )

        if response.status_code == 429:
            return {"error": "Rate limited", "suggestion": "Wait 60 seconds and retry", "status_code": 429}

        if response.status_code != 200:
            return {"step": "restaurants_information", "error": "Failed", "status_code": response.status_code, "detail": response.text}

        return {"success": True, "status_code": response.status_code, "response": response.json()}

    except Exception as e:
        return {"error": str(e), "error_type": type(e).__name__}


@app.get("/restaurants", tags=["Restaurants"])
async def get_restaurants():
    """Fetch all restaurant groups."""
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
    """Fetch details of the restaurant set in RESTAURANT_GUID."""
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


# ── ORDERS ─────────────────────────────────────────────────

@app.get("/orders/today", tags=["Orders"])
async def get_orders_today():
    """Fetch all orders for today's business date."""
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
    """Fetch orders between a start and end datetime."""
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
    """Fetch orders for a specific business date (YYYYMMDD)."""
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
    """Fetch a single order by its GUID."""
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


# ── PAYMENTS ───────────────────────────────────────────────

@app.get("/payments", tags=["Payments"])
async def get_payments(paidBusinessDate: str = None):
    """Fetch payment records. Optionally filter by paidBusinessDate (YYYYMMDD)."""
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


# ── METRICS ────────────────────────────────────────────────

@app.get("/metrics", tags=["Metrics"])
async def get_metrics(
    startBusinessDate: str = "20260401",
    endBusinessDate: str   = "20260407"
):
    """
    Workflow:
    1. Login (cached token)
    2. Fetch restaurant IDs from /era/v1/restaurants-information
    3. POST /era/v1/metrics → job ID
    4. Poll until ready → return metrics
    """
    try:
        token = await get_auth_token()

        common_headers = {
            "Authorization": f"Bearer {token}",
            "Toast-Management-Group-External-ID": TOAST_MANAGEMENT_GROUP_EXTERNAL_ID,
            "Content-Type": "application/json"
        }

        # Fetch restaurant IDs
        async with httpx.AsyncClient() as client:
            restaurants_response = await client.get(
                f"{TOAST_BASE_URL}/era/v1/restaurants-information",
                headers=common_headers
            )

        if restaurants_response.status_code == 429:
            return {
                "step": "restaurants_information",
                "error": "Rate limited — use /metrics/from-env or /metrics/custom instead",
                "status_code": 429
            }

        if restaurants_response.status_code != 200:
            return {"step": "restaurants_information", "error": "Failed to fetch restaurants", "status_code": restaurants_response.status_code, "detail": restaurants_response.text}

        restaurants_data = restaurants_response.json()
        restaurant_ids = [
            r["restaurantGuid"]
            for r in restaurants_data
            if r.get("active") and not r.get("archived")
        ]

        if not restaurant_ids:
            return {"step": "restaurant_ids_extraction", "error": "No active restaurants found"}

        # POST metrics
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
            return {"step": "metrics_post", "error": "Failed", "status_code": metrics_response.status_code, "detail": metrics_response.text}

        job_id = metrics_response.json()

        poll_result = await poll_metrics_result(job_id, common_headers)
        if not poll_result.get("success"):
            return {**poll_result, "job_id": job_id}

        return {
            "success": True,
            "startBusinessDate": startBusinessDate,
            "endBusinessDate": endBusinessDate,
            "total_restaurants": len(restaurant_ids),
            "restaurant_ids": restaurant_ids,
            "job_id": job_id,
            "metrics": poll_result["result"]
        }

    except httpx.ConnectError as e:
        return {"step": "network", "error": "Connection error", "detail": str(e)}
    except httpx.TimeoutException as e:
        return {"step": "network", "error": "Request timed out", "detail": str(e)}
    except Exception as e:
        return {"step": "unknown", "error": str(e), "error_type": type(e).__name__}


@app.get("/metrics/from-env", tags=["Metrics"])
async def get_metrics_from_env(
    startBusinessDate: str = "20260401",
    endBusinessDate: str   = "20260407"
):
    """
    Skips restaurant fetch — uses RESTAURANT_IDS from env directly.
    Workflow:
    1. Login (cached token)
    2. Use RESTAURANT_IDS from env
    3. POST /era/v1/metrics → job ID
    4. Poll until ready → return metrics
    """
    try:
        if not RESTAURANT_IDS:
            return {
                "error": "RESTAURANT_IDS env variable is empty or not set",
                "suggestion": 'Set it in Vercel as: RESTAURANT_IDS=["id-1","id-2"]'
            }

        token = await get_auth_token()

        common_headers = {
            "Authorization": f"Bearer {token}",
            "Toast-Management-Group-External-ID": TOAST_MANAGEMENT_GROUP_EXTERNAL_ID,
            "Content-Type": "application/json"
        }

        # POST metrics with env IDs directly
        async with httpx.AsyncClient() as client:
            metrics_response = await client.post(
                f"{TOAST_BASE_URL}/era/v1/metrics",
                headers=common_headers,
                json={
                    "startBusinessDate": startBusinessDate,
                    "endBusinessDate": endBusinessDate,
                    "restaurantIds": RESTAURANT_IDS,
                    "excludedRestaurantIds": []
                }
            )

        if metrics_response.status_code == 429:
            return {"step": "metrics_post", "error": "Rate limited", "suggestion": "Wait 60 seconds and retry", "status_code": 429}

        if metrics_response.status_code != 200:
            return {"step": "metrics_post", "error": "Failed", "status_code": metrics_response.status_code, "detail": metrics_response.text}

        job_id = metrics_response.json()

        if not job_id:
            return {"step": "metrics_post", "error": "No job ID returned", "raw": metrics_response.text}

        # Poll for result
        poll_result = await poll_metrics_result(job_id, common_headers)

        if not poll_result.get("success"):
            return {**poll_result, "job_id": job_id}

        return {
            "success": True,
            "source": "env",
            "startBusinessDate": startBusinessDate,
            "endBusinessDate": endBusinessDate,
            "total_restaurants": len(RESTAURANT_IDS),
            "restaurant_ids": RESTAURANT_IDS,
            "job_id": job_id,
            "metrics": poll_result["result"]
        }

    except httpx.ConnectError as e:
        return {"step": "network", "error": "Connection error", "detail": str(e)}
    except httpx.TimeoutException as e:
        return {"step": "network", "error": "Request timed out", "detail": str(e)}
    except Exception as e:
        return {"step": "unknown", "error": str(e), "error_type": type(e).__name__}


@app.post("/metrics/custom", tags=["Metrics"])
async def get_metrics_custom(request: MetricsCustomRequest):
    """
    Skips restaurant fetch — pass restaurant IDs directly in body.
    """
    try:
        token = await get_auth_token()

        common_headers = {
            "Authorization": f"Bearer {token}",
            "Toast-Management-Group-External-ID": TOAST_MANAGEMENT_GROUP_EXTERNAL_ID,
            "Content-Type": "application/json"
        }

        async with httpx.AsyncClient() as client:
            metrics_response = await client.post(
                f"{TOAST_BASE_URL}/era/v1/metrics",
                headers=common_headers,
                json={
                    "startBusinessDate": request.startBusinessDate,
                    "endBusinessDate": request.endBusinessDate,
                    "restaurantIds": request.restaurantIds,
                    "excludedRestaurantIds": request.excludedRestaurantIds
                }
            )

        if metrics_response.status_code != 200:
            return {"step": "metrics_post", "error": "Failed", "status_code": metrics_response.status_code, "detail": metrics_response.text}

        job_id = metrics_response.json()

        poll_result = await poll_metrics_result(job_id, common_headers)
        if not poll_result.get("success"):
            return {**poll_result, "job_id": job_id}

        return {
            "success": True,
            "source": "custom_body",
            "startBusinessDate": request.startBusinessDate,
            "endBusinessDate": request.endBusinessDate,
            "total_restaurants": len(request.restaurantIds),
            "restaurant_ids": request.restaurantIds,
            "job_id": job_id,
            "metrics": poll_result["result"]
        }

    except httpx.ConnectError as e:
        return {"step": "network", "error": "Connection error", "detail": str(e)}
    except httpx.TimeoutException as e:
        return {"step": "network", "error": "Request timed out", "detail": str(e)}
    except Exception as e:
        return {"step": "unknown", "error": str(e), "error_type": type(e).__name__}


@app.get("/metrics/result/{job_id}", tags=["Metrics"])
async def get_metrics_result(job_id: str):
    """Manually fetch metrics result using a job ID."""
    try:
        token = await get_auth_token()
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{TOAST_BASE_URL}/era/v1/metrics/{job_id}/",
                headers={
                    "Authorization": f"Bearer {token}",
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
        return {"error": str(e), "error_type": type(e).__name__}