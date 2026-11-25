"""Flight-booking example service."""

import os
import sys
import json
import urllib.request
import urllib.parse
from typing import Dict, Any, List, Optional
from nitrostack import injectable


@injectable()
class DuffelService:
    def __init__(self):
        self.api_key = os.environ.get("DUFFEL_API_KEY")
        self.is_mock = not self.api_key or self.api_key.startswith("your-") or len(self.api_key) < 5

    def _request(self, method: str, path: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if self.is_mock:
            raise ValueError("Duffel API key is not configured. Running in mock mode.")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Duffel-Version": "v1",
            "Content-Type": "application/json"
        }
        url = f"https://api.duffel.com{path}"
        req_data = json.dumps({"data": data}).encode("utf-8") if data else None

        req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                res_payload = json.loads(response.read().decode("utf-8"))
                return res_payload.get("data", {})
        except Exception as e:
            sys.stderr.write(f"Duffel API Request failed: {e}\n")
            sys.stderr.flush()
            raise e

    async def search_flights(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if self.is_mock:
            return {
                "id": "orq_mock123456",
                "offers": [
                    {
                        "id": "off_mock123456",
                        "total_amount": "450.00",
                        "total_currency": "USD",
                        "expires_at": "2026-12-31T12:00:00Z",
                        "slices": [
                            {
                                "origin": {"iata_code": params["origin"], "name": "Origin Airport", "city_name": "Origin City"},
                                "destination": {"iata_code": params["destination"], "name": "Dest Airport", "city_name": "Dest City"},
                                "duration": "PT6H30M",
                                "segments": [
                                    {
                                        "id": "seg_outbound",
                                        "origin": {"iata_code": params["origin"]},
                                        "destination": {"iata_code": params["destination"]},
                                        "departing_at": f"{params['departureDate']}T08:00:00Z",
                                        "arriving_at": f"{params['departureDate']}T14:30:00Z",
                                        "marketing_carrier": {"name": "Mock Airlines"},
                                        "marketing_carrier_flight_number": "MK123",
                                        "aircraft": {"name": "Boeing 787"}
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }

        slices = [{"origin": params["origin"], "destination": params["destination"], "departure_date": params["departureDate"]}]
        if params.get("returnDate"):
            slices.append({"origin": params["destination"], "destination": params["origin"], "departure_date": params["returnDate"]})

        duffel_params = {
            "slices": slices,
            "passengers": params.get("passengers", [{"type": "adult"}]),
            "cabin_class": params.get("cabinClass", "economy"),
            "return_offers": True
        }
        res = self._request("POST", "/offer_requests", duffel_params)
        return {
            "id": res.get("id"),
            "offers": res.get("offers", []),
            "passengers": res.get("passengers", []),
            "slices": res.get("slices", [])
        }

    async def get_offer(self, offer_id: str) -> Dict[str, Any]:
        if self.is_mock:
            return {
                "id": offer_id,
                "total_amount": "450.00",
                "total_currency": "USD",
                "expires_at": "2026-12-31T12:00:00Z",
                "slices": [
                    {
                        "origin": {"iata_code": "JFK", "name": "John F. Kennedy Airport", "city_name": "New York"},
                        "destination": {"iata_code": "LAX", "name": "Los Angeles Airport", "city_name": "Los Angeles"},
                        "duration": "PT6H30M",
                        "segments": [
                            {
                                "id": "seg_mock",
                                "origin": {"iata_code": "JFK"},
                                "destination": {"iata_code": "LAX"},
                                "departing_at": "2026-07-15T08:00:00Z",
                                "arriving_at": "2026-07-15T14:30:00Z",
                                "marketing_carrier": {"name": "Mock Airlines"},
                                "marketing_carrier_flight_number": "MK123",
                                "aircraft": {"name": "Boeing 787"}
                            }
                        ]
                    }
                ]
            }
        return self._request("GET", f"/offers/{offer_id}")

    async def get_seats_for_offer(self, offer_id: str) -> List[Dict[str, Any]]:
        if self.is_mock:
            return [
                {
                    "cabin_class": "economy",
                    "rows": [
                        {
                            "row_number": 10,
                            "sections": [
                                {
                                    "elements": [
                                        {
                                            "type": "seat",
                                            "id": "seat_10a",
                                            "designator": "10A",
                                            "available_services": [{"total_amount": "25.00", "total_currency": "USD"}],
                                            "disclosures": ["window"]
                                        },
                                        {
                                            "type": "seat",
                                            "id": "seat_10b",
                                            "designator": "10B",
                                            "available_services": [{"total_amount": "0.00", "total_currency": "USD"}],
                                            "disclosures": ["middle"]
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            ]
        res = self._request("GET", f"/seat_maps?offer_id={offer_id}")
        return res if isinstance(res, list) else []

    async def create_order(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if self.is_mock:
            return {
                "id": "ord_mock123456",
                "status": "held",
                "total_amount": "450.00",
                "total_currency": "USD",
                "expires_at": "2026-12-31T12:00:00Z",
                "booking_reference": "ABCXYZ",
                "passengers": [
                    {
                        "id": f"pax_{idx}",
                        "given_name": p["given_name"],
                        "family_name": p["family_name"],
                        "type": "adult"
                    } for idx, p in enumerate(params["passengers"])
                ],
                "slices": [
                    {
                        "origin": {"iata_code": "JFK"},
                        "destination": {"iata_code": "LAX"},
                        "duration": "PT6H30M",
                        "segments": [
                            {
                                "departing_at": "2026-07-15T08:00:00Z",
                                "arriving_at": "2026-07-15T14:30:00Z"
                            }
                        ]
                    }
                ]
            }

        order_payload = {
            "selected_offers": params["selectedOffers"],
            "passengers": params["passengers"],
            "type": "hold"
        }
        return self._request("POST", "/orders", order_payload)

    async def get_order(self, order_id: str) -> Dict[str, Any]:
        if self.is_mock:
            return {
                "id": order_id,
                "status": "held",
                "total_amount": "450.00",
                "total_currency": "USD",
                "booking_reference": "ABCXYZ",
                "created_at": "2026-06-25T12:00:00Z",
                "expires_at": "2026-12-31T12:00:00Z",
                "passengers": [
                    {
                        "id": "pax_0",
                        "given_name": "John",
                        "family_name": "Doe",
                        "type": "adult",
                        "email": "john@example.com",
                        "phone_number": "+1234567890"
                    }
                ],
                "slices": [
                    {
                        "id": "sli_mock",
                        "origin": {"iata_code": "JFK", "name": "John F. Kennedy Airport", "city_name": "New York"},
                        "destination": {"iata_code": "LAX", "name": "Los Angeles Airport", "city_name": "Los Angeles"},
                        "duration": "PT6H30M",
                        "segments": [
                            {
                                "id": "seg_mock",
                                "origin": {"iata_code": "JFK"},
                                "destination": {"iata_code": "LAX"},
                                "departing_at": "2026-07-15T08:00:00Z",
                                "arriving_at": "2026-07-15T14:30:00Z",
                                "marketing_carrier": {"name": "Mock Airlines"},
                                "marketing_carrier_flight_number": "MK123",
                                "aircraft": {"name": "Boeing 787"}
                            }
                        ]
                    }
                ]
            }
        return self._request("GET", f"/orders/{order_id}")

    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        if self.is_mock:
            return {
                "id": "ocr_mock123456",
                "refund_amount": "450.00",
                "refund_currency": "USD",
                "confirmed_at": "2026-06-25T12:30:00Z"
            }
        cancel_payload = {"order_id": order_id}
        return self._request("POST", "/order_cancellations", cancel_payload)

    async def get_airlines(self) -> List[Dict[str, Any]]:
        if self.is_mock:
            return [
                {"iata_code": "AA", "name": "American Airlines"},
                {"iata_code": "DL", "name": "Delta Air Lines"},
                {"iata_code": "UA", "name": "United Airlines"},
                {"iata_code": "BA", "name": "British Airways"}
            ]
        res = self._request("GET", "/airlines")
        return res if isinstance(res, list) else []
