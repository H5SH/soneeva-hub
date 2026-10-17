"""Twilio REST over httpx. The master credentials act on subaccounts through the URL."""

from typing import Any

import httpx

from src.numbers.base import AvailableNumber, NumberProvider, NumbersError, Subaccount

API = "https://api.twilio.com/2010-04-01"


class TwilioNumbers(NumberProvider):
    def __init__(self, account_sid: str, auth_token: str) -> None:
        self._sid = account_sid
        self._auth = (account_sid, auth_token)

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if not self._sid:
            raise NumbersError("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN are not set")
        async with httpx.AsyncClient(auth=self._auth, timeout=20.0) as client:
            response = await client.request(method, f"{API}{path}", **kwargs)
        if response.status_code >= 400:
            raise NumbersError(response.json().get("message", response.text))
        return response.json()

    async def search(self, country: str, contains: str, kind: str, limit: int) -> list[AvailableNumber]:
        params = {"VoiceEnabled": "true", "PageSize": str(limit)}
        if contains:
            params["Contains"] = contains
        data = await self._request(
            "GET", f"/Accounts/{self._sid}/AvailablePhoneNumbers/{country.upper()}/{kind}.json",
            params=params,
        )
        return [
            AvailableNumber(phoneNumber=n["phone_number"], friendlyName=n.get("friendly_name", ""),
                            locality=n.get("locality") or "", region=n.get("region") or "",
                            country=n.get("iso_country", country.upper()))
            for n in data.get("available_phone_numbers", [])
        ]

    async def create_subaccount(self, name: str) -> Subaccount:
        data = await self._request("POST", "/Accounts.json", data={"FriendlyName": name})
        return Subaccount(sid=data["sid"], token=data["auth_token"])

    async def buy(self, account_sid: str, phone: str, voice_url: str, status_url: str) -> str:
        data = await self._request(
            "POST", f"/Accounts/{account_sid}/IncomingPhoneNumbers.json",
            data={"PhoneNumber": phone, "VoiceUrl": voice_url, "VoiceMethod": "POST",
                  "StatusCallback": status_url, "StatusCallbackMethod": "POST"},
        )
        return data["sid"]
