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
        "RESTAURANT_GUID": RESTAURANT_GUID
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