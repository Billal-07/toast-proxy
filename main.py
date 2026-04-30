from fastapi import FastAPI, Request, HTTPException
import httpx

app = FastAPI()

TOAST_BASE_URL = "https://ws-api.toasttab.com"

@app.post("/toast-auth")
async def toast_auth(request: Request):
    body = await request.json()     
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{TOAST_BASE_URL}/authentication/v1/authentication/login",
            json=body,
            headers={"Content-Type": "application/json"}
        )
    return response.json()


@app.get("/toast-orders")
async def toast_orders(request: Request):
    # Forward all query params (startDate, endDate, businessDate etc.)
    params = dict(request.query_params)
    token = request.headers.get("Authorization")
    restaurant_id = request.headers.get("Toast-Restaurant-External-ID")

    if not token or not restaurant_id:
        raise HTTPException(status_code=400, detail="Missing Authorization or Toast-Restaurant-External-ID header")

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{TOAST_BASE_URL}/orders/v2/ordersBulk",
            params=params,
            headers={
                "Authorization": token,
                "Toast-Restaurant-External-ID": restaurant_id
            }
        )
    return response.json()


@app.get("/toast-restaurants")
async def toast_restaurants(request: Request):
    token = request.headers.get("Authorization")

    if not token:
        raise HTTPException(status_code=400, detail="Missing Authorization header")

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{TOAST_BASE_URL}/restaurants/v1/groups",
            headers={"Authorization": token}
        )
    return response.json()


@app.get("/health")
async def health():
    return {"status": "ok"}