import os
os.environ["CARECIRCLE_DB"] = ":memory:"
os.environ["CARECIRCLE_DEMO_NOW"] = "2026-10-23T08:05"
from carecircle import service as s

print(s.daily_plan()["spoken"])
print(s.confirm_medication("blood pressure pill")["spoken"])
print(s.confirm_medication("amlodipine")["spoken"])
print(s.confirm_medication("vitamin")["status"], s.confirm_medication("metformin")["status"])
print(s.confirm_medication("the sugar medicine")["spoken"])
print(s.confirm_medication("statin")["spoken"])
print(s.check_in(2, 0, "lonely")["spoken"])
print(s.check_in(2, 8, "knee")["spoken"])
print(s.add_reminder("water the plants", "15:00")["spoken"])
print(s.family_digest()["headline"])
print(s.daily_plan()["spoken"])
