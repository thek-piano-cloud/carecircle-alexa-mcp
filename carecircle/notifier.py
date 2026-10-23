"""Optional real-world delivery of family alerts through Amazon SNS.

Without configuration CareCircle only records alerts in its outbox (what the dashboard shows).
Set ``CARECIRCLE_SNS_TOPIC_ARN`` (and AWS credentials the usual boto3 way) and every alert is also
published to that SNS topic, where the family subscribes by SMS, email, mobile push or a Lambda that
places a phone call.  Urgency is sent as a message attribute so subscriptions can filter on it
(``{"urgency": ["urgent"]}`` -> phone-call Lambda).

Delivery problems never break the tool call: the result is reported back in the tool output instead.
"""
from __future__ import annotations

import os
from typing import Any

_client: Any = None


def configured() -> bool:
    return bool(os.environ.get("CARECIRCLE_SNS_TOPIC_ARN"))


def set_client(client: Any) -> None:
    """Inject a boto3 SNS client (used by tests)."""
    global _client
    _client = client


def _sns() -> Any:
    global _client
    if _client is None:
        import boto3  # imported lazily: boto3 is optional

        _client = boto3.client("sns", region_name=os.environ.get("AWS_REGION", "ap-northeast-2"))
    return _client


def deliver(urgency: str, message: str) -> dict[str, Any]:
    """Publish one alert. Returns {"channel": "sns", "ok": bool, ...} or {"channel": "outbox-only"}."""
    if not configured():
        return {"channel": "outbox-only"}
    try:
        resp = _sns().publish(
            TopicArn=os.environ["CARECIRCLE_SNS_TOPIC_ARN"],
            Subject=f"CareCircle [{urgency}]"[:99],
            Message=message,
            MessageAttributes={"urgency": {"DataType": "String", "StringValue": urgency}},
        )
        return {"channel": "sns", "ok": True, "message_id": resp.get("MessageId")}
    except Exception as exc:  # noqa: BLE001 - never let a delivery error break the care flow
        return {"channel": "sns", "ok": False, "error": type(exc).__name__}
