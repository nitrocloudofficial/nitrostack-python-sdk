import os
import sys
import json
import urllib.request
import urllib.parse
from typing import List, Dict, Any, Optional
from nitrostack import injectable
from nitrostack.widgets.flight_catalog import lookup_airport, search_mock_airports
from nitrostack.widgets.flight_transforms import build_passengers


def _is_mock_key(api_key: Optional[str]) -> bool:
    if not api_key:
        return True
    key = api_key.strip()
    if len(key) < 5:
        return True
    lowered = key.lower()
    return lowered.startswith("your-") or "dummy" in lowered or lowered in {"test", "changeme", "placeholder"}


def _place(code: str) -> Dict[str, Any]:
    airport = lookup_airport(code)
    iata = (code or "").upper()
    if airport:
        return {
            "iata_code": airport["iata_code"],
            "name": airport["name"],
            "city_name": airport["city_name"],
        }
    return {"iata_code": iata, "name": f"{iata} Airport", "city_name": iata}


@injectable()
class DuffelService:
    def __init__(self):
        self.api_key = os.environ.get("DUFFEL_API_KEY")
        self.is_mock = _is_mock_key(self.api_key)
        self._last_search: Dict[str, Any] = {}

    def _request(self, method: str, path: str, data: Optional[Dict[str, Any]] = None) -> Any:
        if self.is_mock:
            raise ValueError("Duffel API key is not configured. Running in mock mode.")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Duffel-Version": "v2",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        url = f"https://api.duffel.com{path}"
        req_data = json.dumps({"data": data}).encode("utf-8") if data else None

        req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                res_payload = json.loads(response.read().decode("utf-8"))
                return res_payload.get("data", {})
        except Exception as e:
            sys.stderr.write(f"Duffel API Request failed: {e}\n")
            sys.stderr.flush()
            raise e

    def _mock_offer(self, offer_id: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        search = params or self._last_search
        origin = str(search.get("origin") or "JFK").upper()
        destination = str(search.get("destination") or "LAX").upper()
        departure = search.get("departureDate") or "2026-09-15"
        return_date = search.get("returnDate")
        origin_place = _place(origin)
        dest_place = _place(destination)

        def slice_for(src: Dict[str, Any], dst: Dict[str, Any], date: str, seg_id: str) -> Dict[str, Any]:
            return {
                "origin": src,
                "destination": dst,
                "duration": "PT6H30M",
                "segments": [
                    {
                        "id": seg_id,
                        "origin": {"iata_code": src["iata_code"]},
                        "destination": {"iata_code": dst["iata_code"]},
                        "departing_at": f"{date}T08:00:00Z",
                        "arriving_at": f"{date}T14:30:00Z",
                        "duration": "PT6H30M",
                        "marketing_carrier": {"name": "Mock Airlines", "iata_code": "MK"},
                        "marketing_carrier_flight_number": "MK123",
                        "aircraft": {"name": "Boeing 787"},
                    }
                ],
            }

        slices = [slice_for(origin_place, dest_place, departure, "seg_outbound")]
        if return_date:
            slices.append(slice_for(dest_place, origin_place, str(return_date), "seg_return"))
        return {
            "id": offer_id,
            "total_amount": "450.00",
            "total_currency": "USD",
            "expires_at": "2026-12-31T12:00:00Z",
            "passenger_identity_documents_required": origin[:1] != destination[:1],
            "conditions": {
                "refund_before_departure": {"allowed": False},
                "change_before_departure": {"allowed": True, "penalty_amount": "75.00", "penalty_currency": "USD"},
            },
            "payment_requirements": {
                "requires_instant_payment": False,
                "price_guarantee_expires_at": "2026-12-31T12:00:00Z",
            },
            "passengers": [{"id": "pas_mock", "type": "adult", "fare_type": "economy", "baggages": [{"type": "checked", "quantity": 1}]}],
            "slices": slices,
        }

    async def search_flights(self, params: Dict[str, Any]) -> Dict[str, Any]:
        origin = str(params.get("origin") or "").upper()
        destination = str(params.get("destination") or "").upper()
        params = {**params, "origin": origin, "destination": destination}
        self._last_search = params
        if self.is_mock:
            offer = self._mock_offer("off_mock123456", params)
            return {"id": "orq_mock123456", "offers": [offer], "passengers": offer.get("passengers", []), "slices": offer.get("slices", [])}

        passengers = params.get("passengers") or build_passengers(
            int(params.get("adults") or 1),
            int(params.get("children") or 0),
            int(params.get("infants") or 0),
        )
        slices: List[Dict[str, Any]] = [
            {
                "origin": origin,
                "destination": destination,
                "departure_date": params["departureDate"],
            }
        ]
        if params.get("departureTime"):
            slices[0]["departure_time"] = params["departureTime"]
        if params.get("returnDate"):
            slices.append(
                {
                    "origin": destination,
                    "destination": origin,
                    "departure_date": params["returnDate"],
                }
            )

        duffel_params: Dict[str, Any] = {
            "slices": slices,
            "passengers": passengers,
            "cabin_class": params.get("cabinClass", "economy"),
            "return_offers": True,
        }
        if params.get("maxConnections") is not None:
            duffel_params["max_connections"] = params["maxConnections"]
        res = self._request("POST", "/air/offer_requests", duffel_params)
        return {
            "id": res.get("id"),
            "offers": res.get("offers", []),
            "passengers": res.get("passengers", []),
            "slices": res.get("slices", []),
        }

    async def get_offer(self, offer_id: str) -> Dict[str, Any]:
        if self.is_mock:
            return self._mock_offer(offer_id)
        return self._request("GET", f"/air/offers/{offer_id}")

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
                                            "disclosures": ["window"],
                                        },
                                        {
                                            "type": "seat",
                                            "id": "seat_10b",
                                            "designator": "10B",
                                            "available_services": [{"total_amount": "0.00", "total_currency": "USD"}],
                                            "disclosures": ["middle"],
                                        },
                                    ]
                                }
                            ],
                        }
                    ],
                }
            ]
        res = self._request("GET", f"/air/seat_maps?offer_id={urllib.parse.quote(offer_id)}")
        return res if isinstance(res, list) else []

    async def create_order(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if self.is_mock:
            offer = self._mock_offer(params.get("selectedOffers", ["off_mock123456"])[0])
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
                        "given_name": p.get("given_name"),
                        "family_name": p.get("family_name"),
                        "type": "adult",
                        "email": p.get("email"),
                        "phone_number": p.get("phone_number"),
                    }
                    for idx, p in enumerate(params.get("passengers") or [])
                ],
                "slices": offer["slices"],
            }

        order_payload = {
            "selected_offers": params["selectedOffers"],
            "passengers": params["passengers"],
            "type": "hold",
        }
        return self._request("POST", "/air/orders", order_payload)

    async def get_order(self, order_id: str) -> Dict[str, Any]:
        if self.is_mock:
            offer = self._mock_offer("off_mock123456")
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
                        "phone_number": "+1234567890",
                    }
                ],
                "slices": offer["slices"],
            }
        return self._request("GET", f"/air/orders/{order_id}")

    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        if self.is_mock:
            return {
                "id": "ocr_mock123456",
                "refund_amount": "450.00",
                "refund_currency": "USD",
                "confirmed_at": "2026-06-25T12:30:00Z",
            }
        return self._request("POST", "/air/order_cancellations", {"order_id": order_id})

    async def get_airlines(self) -> List[Dict[str, Any]]:
        if self.is_mock:
            return [
                {"iata_code": "AA", "name": "American Airlines"},
                {"iata_code": "DL", "name": "Delta Air Lines"},
                {"iata_code": "UA", "name": "United Airlines"},
                {"iata_code": "BA", "name": "British Airways"},
                {"iata_code": "AI", "name": "Air India"},
            ]
        res = self._request("GET", "/air/airlines")
        return res if isinstance(res, list) else []

    async def search_airports(self, query: str) -> List[Dict[str, Any]]:
        """TS: ``duffel.suggestions.list({ query })``. Mock uses the same catalog shape."""
        if self.is_mock:
            return search_mock_airports(query)

        quoted = urllib.parse.quote(query or "")
        res = self._request("GET", f"/places/suggestions?query={quoted}")
        return res if isinstance(res, list) else []
