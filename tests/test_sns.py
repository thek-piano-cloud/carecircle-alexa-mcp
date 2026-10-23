"""SNS delivery of family alerts, checked against botocore's Stubber (no AWS account needed).

Run:  python tests/test_sns.py
"""
import os
import sys
from pathlib import Path

os.environ["CARECIRCLE_DB"] = ":memory:"
os.environ["CARECIRCLE_DEMO_NOW"] = "2026-10-23T19:30"
os.environ["CARECIRCLE_SNS_TOPIC_ARN"] = "arn:aws:sns:ap-northeast-2:123456789012:carecircle-alerts"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3
from botocore.stub import Stubber

from carecircle import notifier, service

client = boto3.client("sns", region_name="ap-northeast-2", aws_access_key_id="x", aws_secret_access_key="x")
stub = Stubber(client)
stub.add_response("publish", {"MessageId": "abc-123"}, {
    "TopicArn": os.environ["CARECIRCLE_SNS_TOPIC_ARN"], "Subject": "CareCircle [urgent]",
    "Message": "Mrs. Han said: \"I fell\" Please call her immediately.",
    "MessageAttributes": {"urgency": {"DataType": "String", "StringValue": "urgent"}}})
stub.add_client_error("publish", service_error_code="AuthorizationError")
stub.activate()
notifier.set_client(client)

first = service.notify_family("Mrs. Han said: \"I fell\" Please call her immediately.", "urgent")["notification"]
assert first["delivery"] == {"channel": "sns", "ok": True, "message_id": "abc-123"}, first["delivery"]
second = service.notify_family("Quick hello", "info")
assert second["notification"]["delivery"]["ok"] is False and second["status"] == "sent"   # failure never breaks the tool
stub.assert_no_pending_responses()
print("PASS  urgent alert published to SNS with urgency attribute")
print("PASS  SNS failure is reported, the care flow still succeeds")
