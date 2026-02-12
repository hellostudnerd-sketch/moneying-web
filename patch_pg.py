# -*- coding: utf-8 -*-

import re



APP = "/home/ubuntu/moneying-web/app.py"

ADMIN = "/home/ubuntu/moneying-web/templates/admin_home.html"

EVENT = "/home/ubuntu/moneying-web/templates/profitguard_event.html"



with open(APP, "r", encoding="utf-8") as f:

    code = f.read()



# 1) EventTrialApply 모델에 phone, user_id 추가

old = """class EventTrialApply(db.Model):

    id = db.Column(db.Integer, primary_key=True)

    email = db.Column(db.String(200), unique=True, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)"""

new = """class EventTrialApply(db.Model):

    id = db.Column(db.Integer, primary_key=True)

    email = db.Column(db.String(200), unique=True, nullable=False)

    phone = db.Column(db.String(20), nullable=True)

    user_id = db.Column(db.Integer, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)"""

if old in code:

    code = code.replace(old, new)

    print("[OK] EventTrialApply model")

