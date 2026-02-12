import os
import requests
import secrets
import uuid
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, abort
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import desc
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)

# Cloudflare 프록시 대응
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET", "moneying-perfect-final-safe")
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True

# ============ 성능 최적화 ============
from flask_compress import Compress
from flask_caching import Cache

# Gzip 압축 (응답 크기 50% 감소)
Compress(app)

# 캐싱 설정
cache = Cache(app, config={
    'CACHE_TYPE': 'simple',
    'CACHE_DEFAULT_TIMEOUT': 300  # 5분
})

DATABASE_URL = os.getenv("DATABASE_URL")
print(f"=== RAW DATABASE_URL: {DATABASE_URL} ===")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
print(f"=== FINAL DATABASE_URL: {DATABASE_URL} ===")
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL or ("sqlite:///" + os.path.join(BASE_DIR, "database.db"))
print(f"=== USING: {app.config['SQLALCHEMY_DATABASE_URI'][:50]}... ===")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# 이메일 설정
MAIL_SERVER = os.getenv("MAIL_SERVER", "smtp.gmail.com")
MAIL_PORT = int(os.getenv("MAIL_PORT", 587))
MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
MAIL_FROM = os.getenv("MAIL_FROM", "noreply@moneying.co.kr")

# ============ [FIX #5,6,7] API 키/비밀번호 환경변수로 통합 ============
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY", "5d819c52ec14510c31d0ed3a676fcfa1")
KAKAO_REDIRECT_URI = os.getenv("KAKAO_REDIRECT_URI", "https://moneying.biz/auth/kakao/callback")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "AIzaSyDRnCHasdEJ3ARExoAsfqmnZiwp1oPrjNQ")

# ============ [FIX #2,8] R2 자격증명 환경변수 + 전역 S3 클라이언트 ============
R2_ENDPOINT = os.getenv("R2_ENDPOINT", "https://b6f9c47a567f57911cab3c58f07cfc61.r2.cloudflarestorage.com")
R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY", "bd378a5b4a8c51dece8aeeec96c846e5")
R2_SECRET_KEY = os.getenv("R2_SECRET_KEY", "4c218d723f2f0e0c122c75fa6d782eb1f659e17eabdecc50dc009bd2edbce0c0")
R2_BUCKET = os.getenv("R2_BUCKET", "moneying-uploads")



# ============ 알리고 알림톡 설정 ============

ALIGO_API_KEY = os.getenv("ALIGO_API_KEY", "")

ALIGO_USER_ID = os.getenv("ALIGO_USER_ID", "")

ALIGO_SENDER_KEY = os.getenv("ALIGO_SENDER_KEY", "")

ALIGO_SENDER = os.getenv("ALIGO_SENDER", "")

ALIGO_TPL_WELCOME = os.getenv("ALIGO_TPL_WELCOME", "")

ALIGO_TPL_GALLERY_SUB = os.getenv("ALIGO_TPL_GALLERY_SUB", "")

ALIGO_TPL_ALLINONE_SUB = os.getenv("ALIGO_TPL_ALLINONE_SUB", "")

ALIGO_TPL_RENEWAL = os.getenv("ALIGO_TPL_RENEWAL", "")

ALIGO_TPL_CANCEL = os.getenv("ALIGO_TPL_CANCEL", "")

ALIGO_TPL_PAYMENT = os.getenv("ALIGO_TPL_PAYMENT", "")

ALIGO_TPL_PAYMENT_FAIL = os.getenv("ALIGO_TPL_PAYMENT_FAIL", "")
ALIGO_TPL_TRIAL = os.getenv("ALIGO_TPL_TRIAL", "")



import boto3
_s3_client = None

def get_s3_client():
    """전역 S3 클라이언트 (재사용으로 성능 향상)"""
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client('s3',
            endpoint_url=R2_ENDPOINT,
            aws_access_key_id=R2_ACCESS_KEY,
            aws_secret_access_key=R2_SECRET_KEY
        )
    return _s3_client

db = SQLAlchemy(app)

# 링크요청 월 제한
LINK_REQUEST_LIMIT_FREE = 1
LINK_REQUEST_LIMIT_TRIAL = 3
LINK_REQUEST_LIMIT_SUBSCRIBER = 10
LINK_REQUEST_LIMIT_ALLINONE = 20



@app.before_request

def check_session_token():

    # 로그인한 사용자만 체크

    user_id = session.get("user_id")

    if not user_id:

        return



    # 정적 파일, API 일부는 스킵

    if request.path.startswith("/static/") or request.path.startswith("/r2/"):

        return



    # 관리자가 아닌 경우만 중복 로그인 체크

    if not is_admin():

        user = User.query.get(user_id)

        if user and user.session_token != session.get("session_token"):

            session.clear()

            flash("다른 기기에서 로그인하여 자동 로그아웃되었습니다.", "error")

            return redirect(url_for("login"))





    # 구독/체험/판매자 상태 실시간 갱신
    user = User.query.get(user_id)

    if user:

        session["is_seller"] = user.is_seller

        user_subs = get_user_subscriptions(user_id)

        if user_subs:

            session["subscriber"] = True

            session["is_trial"] = False

        elif is_trial_active(user_id):

            session["is_trial"] = True

            session["subscriber"] = False

            print(f"[DEBUG] is_trial=True for user {user_id}")

        else:

            session["is_trial"] = False

            print(f"[DEBUG] is_trial=False, no sub, no trial for user {user_id}")

            session["subscriber"] = False



@app.after_request
def add_header(response):
    # 정적 파일 캐싱 (CSS, JS, 이미지)
    if request.path.startswith('/static/'):
        response.headers["Cache-Control"] = "public, max-age=3600"  # 1시간
    elif 'text/html' in response.content_type:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

    
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def send_email(to_email, subject, html_body):
    """이메일 발송 함수"""
    if not MAIL_USERNAME or not MAIL_PASSWORD:
        print(f"[EMAIL] 설정 없음 - To: {to_email}, Subject: {subject}")
        return False
    
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = MAIL_FROM
        msg["To"] = to_email
        
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        
        with smtplib.SMTP(MAIL_SERVER, MAIL_PORT) as server:
            server.starttls()
            server.login(MAIL_USERNAME, MAIL_PASSWORD)
            server.sendmail(MAIL_FROM, to_email, msg.as_string())
        
        print(f"[EMAIL] 발송 성공 - To: {to_email}")
        return True
    except Exception as e:
        print(f"[EMAIL] 발송 실패 - {e}")
        return False


# ----------------------------
# Models
# ----------------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200), unique=True, nullable=False)
    pw_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # 무료 체험
    free_trial_used = db.Column(db.Boolean, default=False)
    free_trial_expires = db.Column(db.DateTime, nullable=True)
    
    # 친구 초대
    referral_code = db.Column(db.String(8), unique=True, nullable=True)
    referred_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    
    # 로그인 실패 잠금
    login_fail_count = db.Column(db.Integer, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)
    
    # 판매자 관련
    is_seller = db.Column(db.Boolean, default=False)
    seller_status = db.Column(db.String(20), default=None)  # pending, approved, rejected
    seller_company = db.Column(db.String(100), nullable=True)
    seller_category = db.Column(db.String(50), nullable=True)
    seller_intro = db.Column(db.Text, nullable=True)
    seller_applied_at = db.Column(db.DateTime, nullable=True)
    seller_approved_at = db.Column(db.DateTime, nullable=True)

    # 카카오 로그인
    kakao_id = db.Column(db.String(50), nullable=True)
    nickname = db.Column(db.String(50), nullable=True)
    profile_photo = db.Column(db.String(500), nullable=True, default="")
    session_token = db.Column(db.String(64), nullable=True)  # 중복 로그인 방지용
    
    # 프로핏가드 기기인증
    profitguard_hwid = db.Column(db.String(100), nullable=True)
    profitguard_hwid_changed_at = db.Column(db.DateTime, nullable=True)
    pg_password_set = db.Column(db.Boolean, default=False)  # 프로핏가드용 비밀번호 설정 여부
    is_staff = db.Column(db.Boolean, default=False)  # 관리자 권한
    phone = db.Column(db.String(20), nullable=True)

    onboarding_done = db.Column(db.Boolean, default=False)

    
class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    emoji = db.Column(db.String(10), nullable=True, default="")
    sort_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    is_system = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False, default="")
    category = db.Column(db.String(50), nullable=True, default="all")
    images_json = db.Column(db.Text, nullable=True, default="[]")
    tags_json = db.Column(db.Text, nullable=True, default="[]")
    links_json = db.Column(db.Text, nullable=True, default="[]")
    coupang_link = db.Column(db.Text, nullable=True, default="")
    view_count = db.Column(db.Integer, default=0)
    is_free = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # 영상 URL
    video_url = db.Column(db.Text, nullable=True, default="")
    video_url2 = db.Column(db.Text, nullable=True, default="")
    video_url3 = db.Column(db.Text, nullable=True, default="")  # [FIX #1] 중복 제거
    preview_video = db.Column(db.Text, nullable=True, default="")  # 미리보기 영상 R2 URL
    
    # 판매자 관련
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    uploaded_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)  # 관리자 업로드 기록
    status = db.Column(db.String(20), default="approved")  # pending, approved, rejected
    is_featured = db.Column(db.Boolean, default=False)  # 메인 추천 게시물
    is_deleted = db.Column(db.Boolean, default=False)  # 소프트 삭제
    
    # [FIX #9] seller relationship 추가 (N+1 쿼리 방지용)
    seller = db.relationship('User', foreign_keys=[seller_id], lazy='joined')

    def to_dict(self):
        def safe_list(s):
            try:
                v = json.loads(s) if s else []
                return v if isinstance(v, list) else []
            except Exception:
                return []
        
        # [FIX #9] relationship 사용으로 추가 쿼리 없음
        author_name = "머닝"
        author_photo = "/static/images/moneying-logo.webp"
        if self.seller_id and self.seller:
            author_name = self.seller.nickname or self.seller.email.split('@')[0]
            author_photo = self.seller.profile_photo or "/static/images/default-profile.png"
        
        return {
            "id": self.id,
            "title": self.title or "",
            "category": self.category or "all",
            "images": safe_list(self.images_json),
            "tags": safe_list(self.tags_json),
            "links": safe_list(self.links_json),
            "coupang_link": self.coupang_link or "",
            "view_count": self.view_count or 0,
            "is_free": self.is_free or False,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "preview_video": self.preview_video or "",
            "author_name": author_name,
            "author_photo": author_photo,
        }


class CommunityPost(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(30), nullable=False, default="free")
    title = db.Column(db.String(200), nullable=False, default="")
    content = db.Column(db.Text, nullable=False, default="")
    author_email = db.Column(db.String(120), nullable=False, default="")
    images_json = db.Column(db.Text, nullable=True, default="[]")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_deal_available = db.Column(db.Boolean, default=True)  # 공구/협찬 진행중 여부
    deal_type = db.Column(db.String(20), nullable=True)  # groupbuy, sponsor
    deal_deadline = db.Column(db.DateTime, nullable=True)
    deal_max_people = db.Column(db.Integer, nullable=True)
    deal_subscribers_only = db.Column(db.Boolean, default=False)
    deal_closed = db.Column(db.Boolean, default=False)
    reward_requested = db.Column(db.Boolean, default=False)  # 수익인증 리워드 신청 여부

    def images(self):
        try:
            v = json.loads(self.images_json or "[]")
            return v if isinstance(v, list) else []
        except Exception:
            return []


class CommunityComment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('community_post.id'), nullable=False)
    author_email = db.Column(db.String(120), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    post = db.relationship('CommunityPost', backref='comments')


class CommunityLike(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('community_post.id'), nullable=False)
    user_email = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('post_id', 'user_email'),)


class LinkRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False, default="")
    original_url = db.Column(db.Text, nullable=False, default="")
    coupang_url = db.Column(db.Text, nullable=True, default="")
    requester_email = db.Column(db.String(120), nullable=False, default="")
    kakao_nickname = db.Column(db.String(100), nullable=True, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def status(self):
        return "완료" if (self.coupang_url or "").strip() else "접수"


class StoreProduct(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False, default="")
    category = db.Column(db.String(50), nullable=False, default="ebook")
    topic = db.Column(db.String(50), nullable=False, default="shortform")
    price = db.Column(db.Integer, nullable=False, default=0)
    description = db.Column(db.Text, nullable=True, default="")
    image = db.Column(db.String(500), nullable=True, default="")
    file_url = db.Column(db.String(500), nullable=True, default="")
    badge = db.Column(db.String(20), nullable=True, default="")
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "title": self.title, "category": self.category,
            "topic": self.topic, "price": self.price, "description": self.description,
            "image": self.image, "file_url": self.file_url, "badge": self.badge,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Subscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    plan_type = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), default="active")
    price = db.Column(db.Integer, default=0)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # 토스페이먼츠 빌링
    billing_key = db.Column(db.String(200), nullable=True)
    customer_key = db.Column(db.String(200), nullable=True)
    
    user = db.relationship('User', backref='subscriptions')
    
    def is_active(self):
        if self.status != "active":
            return False
        if self.expires_at is None:
            return True
        return self.expires_at > datetime.utcnow()


class PaymentHistory(db.Model):
    """결제 기록"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    subscription_id = db.Column(db.Integer, db.ForeignKey('subscription.id'), nullable=True)
    order_id = db.Column(db.String(100), unique=True, nullable=False)
    payment_key = db.Column(db.String(200), nullable=True)
    amount = db.Column(db.Integer, nullable=False)
    plan_type = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), default="pending")
    paid_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class GroupBuy(db.Model):
    """공구/협찬 모델"""
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    image = db.Column(db.String(500), nullable=True)
    category = db.Column(db.String(50), default="groupbuy")
    
    max_participants = db.Column(db.Integer, default=0)
    subscribers_only = db.Column(db.Boolean, default=True)
    
    start_date = db.Column(db.DateTime, default=datetime.utcnow)
    end_date = db.Column(db.DateTime, nullable=True)
    
    status = db.Column(db.String(20), default="open")
    
    brand = db.Column(db.String(100), nullable=True)
    benefit = db.Column(db.Text, nullable=True)
    requirements = db.Column(db.Text, nullable=True)
    contact = db.Column(db.String(200), nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def current_count(self):
        return GroupBuyApplication.query.filter_by(groupbuy_id=self.id).count()
    
    def is_full(self):
        if self.max_participants == 0:
            return False
        return self.current_count() >= self.max_participants
    
    def is_ended(self):
        if self.status == "ended":
            return True
        if self.end_date and datetime.now() > self.end_date:
            return True
        return False


class GroupBuyApplication(db.Model):
    """공구/협찬 신청"""
    id = db.Column(db.Integer, primary_key=True)
    groupbuy_id = db.Column(db.Integer, db.ForeignKey('group_buy.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    name = db.Column(db.String(50), nullable=True)
    phone = db.Column(db.String(20), nullable=True)

    sns_url = db.Column(db.String(500), nullable=True)
    message = db.Column(db.Text, nullable=True)
    
    status = db.Column(db.String(20), default="pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    groupbuy = db.relationship('GroupBuy', backref='applications')
    user = db.relationship('User', backref='groupbuy_applications')
    
    __table_args__ = (db.UniqueConstraint('groupbuy_id', 'user_id'),)


class DealApplication(db.Model):
    """공구/협찬 신청 (커뮤니티용)"""
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('community_post.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    user_email = db.Column(db.String(120), nullable=False)
    
    name = db.Column(db.String(50), nullable=True)
    phone = db.Column(db.String(20), nullable=True)

    sns_url = db.Column(db.String(500), nullable=True)
    message = db.Column(db.Text, nullable=True)
    
    status = db.Column(db.String(20), default="pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    post = db.relationship('CommunityPost', backref='applications')
    user = db.relationship('User', backref='deal_applications')
    
    __table_args__ = (db.UniqueConstraint('post_id', 'user_id'),)


class Report(db.Model):
    """신고 모델"""
    id = db.Column(db.Integer, primary_key=True)
    reporter_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    target_type = db.Column(db.String(20), nullable=False)
    target_id = db.Column(db.Integer, nullable=False)
    
    reason = db.Column(db.String(50), nullable=False)
    description = db.Column(db.Text, nullable=True)
    
    status = db.Column(db.String(20), default="pending")
    admin_note = db.Column(db.Text, nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    resolved_at = db.Column(db.DateTime, nullable=True)
    
    reporter = db.relationship('User', backref='reports_made')


class UserBlock(db.Model):
    """차단 모델"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    reason = db.Column(db.String(100), nullable=True)
    blocked_until = db.Column(db.DateTime, nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    
    user = db.relationship('User', foreign_keys=[user_id], backref='blocks')


class RevenueRewardHistory(db.Model):
    """수익인증 리워드 신청 기록 (악용 방지)"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    post_id = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default="pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Notification(db.Model):
    """알림 모델"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    type = db.Column(db.String(50), nullable=True)
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=True)
    link = db.Column(db.String(500), nullable=True)
    
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref='notifications')


class EventTrialApply(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200), unique=True, nullable=False)
    phone = db.Column(db.String(20), nullable=True)

    user_id = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)



# ----------------------------

# Template Helpers
# ----------------------------
def get_nickname(email):
    """이메일로 닉네임 조회"""
    try:
        user = User.query.filter_by(email=email).first()
        if user and user.nickname:
            return user.nickname
    except:
        pass
    return email.split('@')[0] if email else '익명'


@app.context_processor
def inject_globals():
    """모든 템플릿에서 사용 가능한 전역 변수/함수"""
    unread_count = 0
    if session.get("user_id"):
        unread_count = Notification.query.filter(Notification.user_id==session["user_id"], Notification.is_read==False, ~Notification.type.like("quest_%")).count()
    return dict(
        get_nickname=get_nickname,
        unread_notifications_count=unread_count
    )


# ----------------------------
# Helpers
# ----------------------------
def is_admin():
    return bool(session.get("admin", False))

def is_logged_in():
    return bool(session.get("user_id"))

def is_subscriber():
    return bool(session.get("subscriber", False))

def save_upload(file_storage):
    """[FIX #2,8] 전역 S3 클라이언트 + 환경변수 사용"""
    if not file_storage or not file_storage.filename:
        return ""
    filename = secure_filename(file_storage.filename)
    ext = os.path.splitext(filename)[1].lower()
    if ext and ext not in ALLOWED_EXT:
        return ""
    
    from io import BytesIO
    from PIL import Image
    
    # 이미지 압축
    img = Image.open(file_storage)
    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')
    
    # 원본용 (1200px)
    img_original = img.copy()
    img_original.thumbnail((1200, 1200), Image.LANCZOS)
    
    # 썸네일용 (400px)
    img_thumb = img.copy()
    img_thumb.thumbnail((400, 400), Image.LANCZOS)
    
    s3 = get_s3_client()
    file_id = uuid.uuid4().hex
    
    # 원본 업로드
    buffer_original = BytesIO()
    img_original.save(buffer_original, 'WEBP', quality=80)
    buffer_original.seek(0)
    s3.upload_fileobj(buffer_original, R2_BUCKET, f"{file_id}.webp", ExtraArgs={'ContentType': 'image/webp'})
    
    # 썸네일 업로드
    buffer_thumb = BytesIO()
    img_thumb.save(buffer_thumb, 'WEBP', quality=70)
    buffer_thumb.seek(0)
    s3.upload_fileobj(buffer_thumb, R2_BUCKET, f"{file_id}_thumb.webp", ExtraArgs={'ContentType': 'image/webp'})
    
    # R2 Public URL 반환 (원본)
    return f"/r2/{file_id}.webp"


# ----------------------------
# 구독 확인 헬퍼
# ----------------------------
def has_active_subscription(user_id, plan_type):
    if not user_id:
        return False
    sub = Subscription.query.filter_by(user_id=user_id, plan_type=plan_type, status="active").first()
    return sub.is_active() if sub else False

def get_user_subscriptions(user_id):
    if not user_id:
        return []
    subs = Subscription.query.filter_by(user_id=user_id, status="active").all()
    return [s for s in subs if s.is_active()]

def can_access_gallery(user_id):
    return has_active_subscription(user_id, "gallery") or has_active_subscription(user_id, "allinone")

def can_access_profitguard(user_id):
    return (has_active_subscription(user_id, "profitguard_lite") or
            has_active_subscription(user_id, "profitguard_pro") or
            has_active_subscription(user_id, "profitguard_lifetime") or
            has_active_subscription(user_id, "allinone"))


# ----------------------------
# 무료 체험 헬퍼
# ----------------------------
def is_trial_active(user_id):
    if not user_id:
        return False
    user = User.query.get(user_id)
    if not user or not user.free_trial_expires:
        return False
    return user.free_trial_expires > datetime.utcnow()

def can_use_free_trial(user_id):
    if not user_id:
        return False
    user = User.query.get(user_id)
    if not user:
        return False
    if user.free_trial_used:
        return False

    if get_user_subscriptions(user_id):

        return False

    # 구독 이력 있으면 체험 차단 (해지 후 재신청 방지)

    all_subs = Subscription.query.filter_by(user_id=user_id).count()

    if all_subs > 0:

        return False



    return True

def get_trial_expires_at(user_id):
    if not user_id:
        return None
    user = User.query.get(user_id)
    if not user:
        return None
    return user.free_trial_expires


# ----------------------------
# 링크요청 제한 헬퍼
# ----------------------------
def get_monthly_link_request_count(user_email):
    if not user_email:
        return 0
    now = datetime.utcnow()
    first_day = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return LinkRequest.query.filter(
        LinkRequest.requester_email == user_email,
        LinkRequest.created_at >= first_day
    ).count()

def get_link_request_limit(user_id):
    if has_active_subscription(user_id, "allinone"):
        return LINK_REQUEST_LIMIT_ALLINONE
    if has_active_subscription(user_id, "gallery"):
        return LINK_REQUEST_LIMIT_SUBSCRIBER
    if has_active_subscription(user_id, "profitguard_lifetime"):

        return LINK_REQUEST_LIMIT_ALLINONE

    if has_active_subscription(user_id, "profitguard_pro") or has_active_subscription(user_id, "profitguard_lite"):

        return LINK_REQUEST_LIMIT_SUBSCRIBER
    if is_trial_active(user_id):
        return LINK_REQUEST_LIMIT_TRIAL
    return LINK_REQUEST_LIMIT_FREE

def can_make_link_request(user_id, user_email):
    return get_monthly_link_request_count(user_email) < get_link_request_limit(user_id)


# ----------------------------
# 세션 업데이트 헬퍼
# ----------------------------
def update_session_status(user_id):
    if not user_id:
        return
    user_subs = get_user_subscriptions(user_id)
    if user_subs:
        session["subscriber"] = True
        session["is_trial"] = False
    elif is_trial_active(user_id):
        session["is_trial"] = True
        session["subscriber"] = False
    else:
        session["is_trial"] = False
        session["subscriber"] = False


# ----------------------------
# R2 이미지 프록시
# ----------------------------
@app.route("/r2/<path:filename>")
def serve_r2_file(filename):
    """[FIX #2,8] 전역 S3 클라이언트 + 환경변수 사용"""
    from flask import Response

    s3 = get_s3_client()

    name, ext = os.path.splitext(filename)
    
    # mp4는 그대로, 나머지는 webp로 변환
    if ext.lower() == '.mp4':
        key = filename
        content_type = 'video/mp4'
    else:
        key = f"{name}.webp"
        content_type = 'image/webp'

    try:
        obj = s3.get_object(Bucket=R2_BUCKET, Key=key)
        return Response(
            obj['Body'].read(),
            content_type=content_type,
            headers={'Cache-Control': 'public, max-age=31536000'}
        )
    except Exception as e:
        print(f"R2 file error for {key}: {e}")
        return f"File not found: {key}", 404


# ----------------------------
# Public Routes
# ----------------------------
@app.route("/")
def index():
    return render_template("index.html", featured_posts=Post.query.filter_by(is_featured=True).filter(Post.is_deleted != True).limit(4).all())

# [FIX #10] store 페이지는 로그인 무관 → 캐시 유지 OK
@app.route("/store")
@cache.cached(timeout=300)
def store():
    products = StoreProduct.query.filter_by(is_active=True).order_by(StoreProduct.id.desc()).all()

    fixed_products = [

        {"id": "ebook", "title": "2차 가공 숏폼 수익화 전자책", "category": "ebook", "price": 29000, "badge": "12억 수익 인증", "image": "/static/images/store-ebook-thumb.svg", "file_url": "/store/ebook"},

        {"id": "profitguard", "title": "프로핏가드 - 품절링크 실시간 감시", "category": "tool", "price": 19000, "badge": "월 구독", "image": "/static/images/store-profitguard-thumb.svg", "file_url": "/profitguard"},

    ]

    return render_template("store.html", products=products, fixed_products=fixed_products)


@app.route("/store/chrome-extension")



@cache.cached(timeout=3600)
def store_chrome_extension():
    return render_template("store_chrome_extension.html")

@app.route("/store/<int:product_id>")
def store_detail(product_id):
    product = StoreProduct.query.get_or_404(product_id)
    if not product.is_active and not is_admin():
        abort(404)
    can_download_pg = False
    is_kakao = False
    pg_pw_set = False
    if session.get('user_id'):
        can_download_pg = can_access_profitguard(session['user_id'])
        u = db.session.get(User, session['user_id'])
        if u and u.kakao_id:
            is_kakao = True
            pg_pw_set = bool(u.pg_password_set)
    return render_template('store_detail.html', product=product, can_download_pg=can_download_pg, is_kakao=is_kakao, pg_pw_set=pg_pw_set)
@app.route("/pricing")
def pricing():
    can_use_trial = False
    active_plans = []
    if session.get('user_id'):
        user = db.session.get(User, session['user_id'])
        if user and not user.free_trial_used:
            can_use_trial = True
        subs = Subscription.query.filter_by(user_id=session['user_id'], status='active').all()
        active_plans = [s.plan_type for s in subs if s.is_active()]
    return render_template('pricing.html', can_use_trial=can_use_trial, active_plans=active_plans)

def send_alimtalk(receiver, subject, message, tpl_code, button=None):

    """알리고 알림톡 발송"""

    if not all([ALIGO_API_KEY, ALIGO_USER_ID, ALIGO_SENDER_KEY, ALIGO_SENDER]):

        print("[알림톡] 설정 미완료 - 건너뜀")

        return {"code": -1, "message": "설정 미완료"}

    if not tpl_code:

        print("[알림톡] 템플릿 미설정 - 건너뜀")

        return {"code": -1, "message": "템플릿 미설정"}

    url = "https://kakaoapi.aligo.in/akv10/alimtalk/send/"

    data = {

        "apikey": ALIGO_API_KEY, "userid": ALIGO_USER_ID,

        "senderkey": ALIGO_SENDER_KEY, "tpl_code": tpl_code,

        "sender": ALIGO_SENDER, "receiver_1": receiver,

        "subject_1": subject, "message_1": message,

        "failover": "N", "testMode": "N",

    }

    if button:

        data["button_1"] = json.dumps(button)

    try:

        resp = requests.post(url, data=data, timeout=10)

        result = resp.json()

        print(f"[알림톡] {subject} -> {receiver}: {result}")

        return result

    except Exception as e:

        print(f"[알림톡] 발송 실패: {e}")

        return {"code": -99, "message": str(e)}





def send_welcome_alimtalk(phone):

    msg = "회원가입이 완료되었습니다.\n\n머닝 서비스 이용을 위해\n로그인 후 마이페이지에서\n이용 내역을 확인하실 수 있습니다."

    button = {"button": [{"name": "채널추가", "linkType": "AC"}, {"name": "마이페이지 바로가기", "linkType": "WL", "linkTypeName": "웹링크", "linkMo": "https://moneying.biz/my", "linkPc": "https://moneying.biz/my"}]}

    return send_alimtalk(phone, "회원가입 완료 안내", msg, ALIGO_TPL_WELCOME, button)





def send_gallery_sub_alimtalk(phone, start_date, next_billing_date):

    msg = f"영상 갤러리 구독이 완료되었습니다.\n\n구독 기간 동안\n영상 콘텐츠 이용이 가능합니다.\n\n구독 시작일: {start_date}\n다음 결제일: {next_billing_date}"

    button = {"button": [{"name": "채널추가", "linkType": "AC"}, {"name": "영상 갤러리 바로가기", "linkType": "WL", "linkTypeName": "웹링크", "linkMo": "https://moneying.biz/gallery", "linkPc": "https://moneying.biz/gallery"}]}

    return send_alimtalk(phone, "영상 갤러리 구독 완료 안내", msg, ALIGO_TPL_GALLERY_SUB, button)





def send_allinone_sub_alimtalk(phone, start_date, next_billing_date):

    msg = f"올인원 패키지 구독이 완료되었습니다.\n\n구성 상품:\n- 영상 갤러리\n- 프로핏가드\n\n구독 시작일: {start_date}\n다음 결제일: {next_billing_date}"

    button = {"button": [{"name": "채널추가", "linkType": "AC"}, {"name": "영상 갤러리 바로가기", "linkType": "WL", "linkTypeName": "웹링크", "linkMo": "https://moneying.biz/gallery", "linkPc": "https://moneying.biz/gallery"}, {"name": "프로핏가드 바로가기", "linkType": "WL", "linkTypeName": "웹링크", "linkMo": "https://moneying.biz/profitguard", "linkPc": "https://moneying.biz/profitguard"}]}

    return send_alimtalk(phone, "올인원 패키지 구독 완료 안내", msg, ALIGO_TPL_ALLINONE_SUB, button)





def send_renewal_alimtalk(phone, subscription_name, amount, paid_at):

    msg = f"구독이 정상적으로 갱신되었습니다.\n\n상품명: {subscription_name}\n결제금액: {amount}\n결제일: {paid_at}"

    button = {"button": [{"name": "채널추가", "linkType": "AC"}, {"name": "마이페이지 바로가기", "linkType": "WL", "linkTypeName": "웹링크", "linkMo": "https://moneying.biz/my", "linkPc": "https://moneying.biz/my"}]}

    return send_alimtalk(phone, "구독 갱신 안내", msg, ALIGO_TPL_RENEWAL, button)





def send_cancel_alimtalk(phone, end_date):

    msg = f"구독 해지가 완료되었습니다.\n\n이용 종료일: {end_date}\n종료일까지 기존 콘텐츠 이용이 가능합니다."

    button = {"button": [{"name": "채널추가", "linkType": "AC"}, {"name": "마이페이지 바로가기", "linkType": "WL", "linkTypeName": "웹링크", "linkMo": "https://moneying.biz/my", "linkPc": "https://moneying.biz/my"}]}

    return send_alimtalk(phone, "구독 해지 완료 안내", msg, ALIGO_TPL_CANCEL, button)





def send_product_payment_alimtalk(phone, product_name, amount, paid_at):

    msg = f"상품 결제가 완료되었습니다.\n\n상품명: {product_name}\n결제금액: {amount}\n결제일시: {paid_at}\n\n구매하신 디지털 자산은\n아래 버튼을 통해 확인하실 수 있습니다.\n\n※ 디지털 상품 특성상\n결제 완료 후 환불이 불가합니다."

    button = {"button": [{"name": "채널추가", "linkType": "AC"}, {"name": "구매 상품 확인", "linkType": "WL", "linkTypeName": "웹링크", "linkMo": "https://moneying.biz/my/payments", "linkPc": "https://moneying.biz/my/payments"}]}

    return send_alimtalk(phone, "상품 결제 완료 안내", msg, ALIGO_TPL_PAYMENT, button)









def send_trial_alimtalk(phone, expire_date):

    msg = f"무료 체험이 시작되었습니다!\n\n체험 기간: {expire_date}까지 (5일)\n\n영상 갤러리와 모든 기능을 자유롭게 이용해보세요."

    button = {"name": "머닝 바로가기", "linkType": "WL", "linkTypeName": "웹링크", "linkMo": "https://moneying.biz/gallery", "linkPc": "https://moneying.biz/gallery"}

    return send_alimtalk(phone, "무료 체험 시작 안내", msg, ALIGO_TPL_TRIAL, button)

def send_payment_fail_alimtalk(phone):

    msg = "구독 결제에 실패하여\n서비스 이용이 제한될 수 있습니다.\n\n결제 수단을 확인해 주세요."

    button = {"button": [{"name": "채널추가", "linkType": "AC"}, {"name": "결제 정보 수정", "linkType": "WL", "linkTypeName": "웹링크", "linkMo": "https://moneying.biz/my/payments", "linkPc": "https://moneying.biz/my/payments"}]}

    return send_alimtalk(phone, "자동결제 실패 안내", msg, ALIGO_TPL_PAYMENT_FAIL, button)





# ============ 토스페이먼츠 결제 ============
PLAN_INFO = {
    'gallery': {'name': '영상 갤러리', 'price': 39000, 'billing': True},
    'allinone': {'name': '올인원 패키지', 'price': 59000, 'billing': True},
    'profitguard_lite': {'name': '프로핏가드 라이트', 'price': 19000, 'billing': True},
    'profitguard_pro': {'name': '프로핏가드 PRO', 'price': 39000, 'billing': True},
    'profitguard_lifetime': {'name': '프로핏가드 평생', 'price': 200000, 'billing': False},
}

@app.route("/checkout/<plan_type>")
def checkout(plan_type):
    if not session.get("user_id"):
        return redirect(url_for("login", next=f"/checkout/{plan_type}"))
    
    plan = PLAN_INFO.get(plan_type)
    if not plan:
        flash("잘못된 요금제입니다.", "error")
        return redirect(url_for("pricing"))
    
    user = db.session.get(User, session["user_id"])
    customer_key = f"cust_{user.id}_{secrets.token_hex(8)}"
    
    return render_template("checkout.html",
        plan_type=plan_type,
        plan=plan,
        customer_key=customer_key,
        client_key=os.getenv("TOSS_CLIENT_KEY", "")
    )

@app.route("/billing/success")
@app.route("/payment/success")

def payment_success():

    """결제위젯 결제 성공 콜백"""

    payment_key = request.args.get("paymentKey")

    order_id = request.args.get("orderId")

    amount = request.args.get("amount")

    plan_type = request.args.get("planType", "")



    if not session.get("user_id") or not payment_key or not order_id or not amount:

        flash("결제 인증에 실패했습니다.", "error")

        return redirect(url_for("pricing"))



    plan = PLAN_INFO.get(plan_type)

    if not plan:

        flash("잘못된 요금제입니다.", "error")

        return redirect(url_for("pricing"))



    if int(amount) != plan['price']:

        flash("결제 금액이 일치하지 않습니다.", "error")

        return redirect(url_for("pricing"))



    import base64

    secret_key = os.getenv("TOSS_SECRET_KEY", "")

    auth_header = base64.b64encode(f"{secret_key}:".encode()).decode()



    confirm_resp = requests.post(

        "https://api.tosspayments.com/v1/payments/confirm",

        headers={

            "Authorization": f"Basic {auth_header}",

            "Content-Type": "application/json"

        },

        json={

            "paymentKey": payment_key,

            "orderId": order_id,

            "amount": int(amount)

        }

    )



    if confirm_resp.status_code != 200:

        error_msg = confirm_resp.json().get("message", "결제 승인 실패")

        flash(f"결제 실패: {error_msg}", "error")

        return redirect(url_for("pricing"))



    pay_data = confirm_resp.json()

    user_id = session["user_id"]

    now = datetime.utcnow()



    sub = Subscription(

        user_id=user_id,

        plan_type=plan_type,

        status="active",

        price=plan['price'],

        started_at=now,

        expires_at=None if not plan['billing'] else now + timedelta(days=30),

        billing_key=None,

        customer_key=None

    )

    db.session.add(sub)



    payment = PaymentHistory(

        user_id=user_id,

        order_id=order_id,

        payment_key=pay_data.get("paymentKey", ""),

        amount=plan['price'],

        plan_type=plan_type,

        status="paid",

        paid_at=now

    )

    db.session.add(payment)

    db.session.commit()



    payment.subscription_id = sub.id

    db.session.commit()



    update_session_status(user_id)



    # 알림톡 발송 (결제 완료)

    try:

        user = db.session.get(User, user_id)

        if user and user.phone:

            send_payment_alimtalk(

                phone=user.phone,

                name=user.nickname or user.email,

                product_name=plan['name'],

                amount=plan['price']

            )

    except Exception as e:

        print(f"[알림톡] 결제 알림 발송 오류: {e}")



    if plan['billing']:

        flash(f"{plan['name']} 구독이 시작되었습니다!", "success")

    else:

        flash(f"{plan['name']} 구매가 완료되었습니다!", "success")

    if 'profitguard' in plan_type:

        return redirect(url_for("profitguard_page"))

    return redirect(url_for("my_page"))



@app.route("/payment/fail")

def payment_fail():

    """결제위젯 결제 실패"""

    error_code = request.args.get("code", "")

    error_msg = request.args.get("message", "결제가 취소되었습니다.")

    flash(f"결제 실패: {error_msg}", "error")

    return redirect(url_for("pricing"))


# [FIX #10] gallery는 사용자별 구독 상태에 따라 다르게 보여야 하므로 캐시 제거




# ============ 관리자 권한 부여/해제 API ============

@app.route("/admin/api/toggle-staff/<int:user_id>", methods=["POST"])

def admin_toggle_staff(user_id):

    if not is_admin():

        return jsonify({"result": "FAIL", "msg": "권한이 없습니다."})

    user = db.session.get(User, user_id)

    if not user:

        return jsonify({"result": "FAIL", "msg": "사용자를 찾을 수 없습니다."})

    user.is_staff = not user.is_staff

    db.session.commit()

    status = "관리자" if user.is_staff else "일반 회원"

    return jsonify({"result": "SUCCESS", "msg": f"{user.nickname or user.email}님이 {status}(으)로 변경되었습니다.", "is_staff": user.is_staff})


# ============ 구독 해지 API ============

@app.route("/api/subscription/cancel", methods=["POST"])

def api_subscription_cancel():

    if not session.get("user_id"):

        return jsonify({"result": "FAIL", "msg": "로그인이 필요합니다."})

    data = request.get_json() or {}

    sub_id = data.get("subscription_id")

    if not sub_id:

        return jsonify({"result": "FAIL", "msg": "구독 정보가 없습니다."})

    sub = Subscription.query.filter_by(id=sub_id, user_id=session["user_id"]).first()

    if not sub:

        return jsonify({"result": "FAIL", "msg": "구독을 찾을 수 없습니다."})

    if sub.plan_type == "profitguard_lifetime":

        return jsonify({"result": "FAIL", "msg": "평생 이용권은 해지할 수 없습니다."})

    sub.status = "cancelled"

    db.session.commit()

    return jsonify({"result": "SUCCESS", "msg": "구독이 해지되었습니다."})



# ============ 프로핏가드 비밀번호 설정 ============

@app.route("/api/profitguard/set-password", methods=["POST"])

def api_profitguard_set_password():

    """카카오 가입자 프로핏가드용 비밀번호 설정"""

    if not session.get("user_id"):

        return jsonify({"result": "FAIL", "msg": "로그인이 필요합니다."})

    user = db.session.get(User, session["user_id"])

    if not user:

        return jsonify({"result": "FAIL", "msg": "유저를 찾을 수 없습니다."})

    password = (request.get_json() or {}).get("password", "").strip()

    if len(password) < 4:

        return jsonify({"result": "FAIL", "msg": "비밀번호는 4자 이상이어야 합니다."})

    user.pw_hash = generate_password_hash(password)

    user.pg_password_set = True

    db.session.commit()

    return jsonify({"result": "SUCCESS", "msg": "비밀번호가 설정되었습니다."})



# ============ 프로핏가드 다운로드 ============



@app.route("/api/profitguard/manual")

def profitguard_manual():

    if not session.get("user_id"):

        flash("로그인이 필요합니다.", "error")

        return redirect(url_for("login"))

    if not can_access_profitguard(session["user_id"]):

        flash("프로핏가드 구독 후 다운로드할 수 있습니다.", "error")

        return redirect(url_for("profitguard_page"))

    manual_url = os.environ.get("PROFITGUARD_MANUAL_URL", "")

    if not manual_url:

        flash("매뉴얼 파일이 준비 중입니다.", "error")

        return redirect(url_for("profitguard_page"))

    return redirect(manual_url)



@app.route("/api/profitguard/download")

def profitguard_download():

    """프로핏가드 exe 다운로드 (구독자 전용)"""

    if not session.get("user_id"):

        flash("로그인이 필요합니다.", "error")

        return redirect(url_for("login"))

    if not can_access_profitguard(session["user_id"]):

        flash("프로핏가드 구독 후 다운로드할 수 있습니다.", "error")

        return redirect(url_for("store_detail", product_id=2))

    # R2에 업로드한 exe 파일 URL로 리다이렉트

    download_url = os.environ.get("PROFITGUARD_DOWNLOAD_URL", "")

    if not download_url:

        flash("다운로드 파일이 준비 중입니다.", "error")

        return redirect(url_for("store_detail", product_id=2))

    return redirect(download_url)



# ============ 프로핏가드 API ============

@app.route("/api/profitguard", methods=["POST"])

def api_profitguard():

    """프로핏가드 exe 통합 API (login, register, reset_device)"""

    data = request.get_json() or {}

    action = data.get("action", "")



    if action == "login":

        email = (data.get("email") or "").strip().lower()

        password = data.get("password", "")

        hwid = data.get("hwid", "")



        if not email or not password:

            return jsonify({"result": "FAIL", "msg": "이메일과 비밀번호를 입력해주세요."})



        user = User.query.filter_by(email=email).first()

        if not user or not check_password_hash(user.pw_hash, password):

            return jsonify({"result": "FAIL", "msg": "이메일 또는 비밀번호가 틀렸습니다."})



        # 구독 상태 확인 (프로핏가드 관련 플랜)

        now = datetime.utcnow()

        tier = "FREE"

        is_trial = False



        # 프로핏가드 구독 확인

        pg_sub = Subscription.query.filter(

            Subscription.user_id == user.id,

            Subscription.status == "active",

            Subscription.plan_type.in_(["profitguard_pro", "profitguard_lite", "profitguard_lifetime", "allinone"])

        ).first()



        if pg_sub and pg_sub.is_active():

            if pg_sub.plan_type in ["profitguard_pro", "allinone", "profitguard_lifetime"]:

                tier = "PRO"

            elif pg_sub.plan_type == "profitguard_lite":

                tier = "BASIC"



        # 무료체험 확인

        if tier == "FREE" and user.free_trial_expires and user.free_trial_expires > now:

            tier = "PRO"

            is_trial = True



        if tier == "FREE":

            return jsonify({"result": "FAIL", "msg": "구독 중인 프로핏가드 플랜이 없습니다.\nmoneying.biz에서 구독 후 이용해주세요."})



        # HWID 기기 인증

        if hwid:

            if not user.profitguard_hwid:

                user.profitguard_hwid = hwid

                db.session.commit()

            elif user.profitguard_hwid != hwid:

                return jsonify({"result": "DEVICE_ERROR", "msg": "이미 다른 기기에 등록되어 있습니다.\n기기 초기화 후 다시 시도해주세요.\n(기기 변경은 월 1회 가능)"})



        user_name = user.nickname or user.email.split("@")[0]

        return jsonify({"result": "SUCCESS", "tier": tier, "is_trial": is_trial, "user_name": user_name})



    elif action == "register":

        email = (data.get("email") or "").strip().lower()

        password = data.get("password", "")

        name = data.get("name", "")

        phone = data.get("phone", "")



        if not email or not password or not name:

            return jsonify({"result": "FAIL", "msg": "이메일, 비밀번호, 이름은 필수입니다."})



        if User.query.filter_by(email=email).first():

            return jsonify({"result": "FAIL", "msg": "이미 가입된 이메일입니다."})



        u = User(

            email=email,

            pw_hash=generate_password_hash(password),

            nickname=name,

            free_trial_used=True,

            free_trial_expires=datetime.utcnow() + timedelta(days=5)

        )

        db.session.add(u)

        db.session.commit()

        return jsonify({"result": "SUCCESS", "msg": "체험판 계정이 생성되었습니다."})



    elif action == "reset_device":

        email = (data.get("email") or "").strip().lower()

        password = data.get("password", "")

        hwid = data.get("hwid", "")



        user = User.query.filter_by(email=email).first()

        if not user or not check_password_hash(user.pw_hash, password):

            return jsonify({"result": "FAIL", "msg": "이메일 또는 비밀번호가 틀렸습니다."})



        # 월 1회 제한

        if user.profitguard_hwid_changed_at:

            days_since = (datetime.utcnow() - user.profitguard_hwid_changed_at).days

            if days_since < 30:

                return jsonify({"result": "FAIL", "msg": f"기기 변경은 월 1회만 가능합니다.\n{30 - days_since}일 후 다시 시도해주세요."})



        user.profitguard_hwid = hwid

        user.profitguard_hwid_changed_at = datetime.utcnow()

        db.session.commit()

        return jsonify({"result": "SUCCESS", "msg": "기기가 변경되었습니다. 다시 로그인해주세요."})



    return jsonify({"result": "FAIL", "msg": "알 수 없는 요청입니다."})



# ============ 스토어 결제 ============

@app.route("/store/checkout/<int:product_id>")

def store_checkout(product_id):

    if not session.get("user_id"):

        return redirect(url_for("login", next=f"/store/checkout/{product_id}"))

    product = db.session.get(StoreProduct, product_id)

    if not product or product.price == 0:

        flash("잘못된 상품입니다.", "error")

        return redirect(url_for("store"))

    user = db.session.get(User, session["user_id"])

    customer_key = f"store_{user.id}_{secrets.token_hex(8)}"

    return render_template("store_checkout.html",

        product=product,

        customer_key=customer_key,

        client_key=os.getenv("TOSS_CLIENT_KEY", "")

    )



@app.route("/store/payment/success")

def store_payment_success():

    payment_key = request.args.get("paymentKey")

    order_id = request.args.get("orderId")

    amount = request.args.get("amount")

    product_id = request.args.get("productId", "")

    if not session.get("user_id") or not payment_key or not order_id or not amount:

        flash("결제 인증에 실패했습니다.", "error")

        return redirect(url_for("store"))

    product = db.session.get(StoreProduct, int(product_id)) if product_id else None

    if not product:

        flash("잘못된 상품입니다.", "error")

        return redirect(url_for("store"))

    if int(amount) != product.price:

        flash("결제 금액이 일치하지 않습니다.", "error")

        return redirect(url_for("store"))

    import base64

    secret_key = os.getenv("TOSS_SECRET_KEY", "")

    auth_header = base64.b64encode(f"{secret_key}:".encode()).decode()

    confirm_resp = requests.post(

        "https://api.tosspayments.com/v1/payments/confirm",

        headers={"Authorization": f"Basic {auth_header}", "Content-Type": "application/json"},

        json={"paymentKey": payment_key, "orderId": order_id, "amount": int(amount)}

    )

    if confirm_resp.status_code != 200:

        error_msg = confirm_resp.json().get("message", "결제 승인 실패")

        flash(f"결제 실패: {error_msg}", "error")

        return redirect(url_for("store_detail", product_id=product.id))

    pay_data = confirm_resp.json()

    user_id = session["user_id"]

    now = datetime.utcnow()

    payment = PaymentHistory(

        user_id=user_id,

        order_id=order_id,

        payment_key=pay_data.get("paymentKey", ""),

        amount=product.price,

        plan_type=f"store_{product.id}",

        status="paid",

        paid_at=now

    )

    db.session.add(payment)

    db.session.commit()

    flash(f"{product.title} 구매가 완료되었습니다!", "success")

    return redirect(url_for("store_detail", product_id=product.id))



@app.route("/store/payment/fail")

def store_payment_fail():

    error_msg = request.args.get("message", "결제가 취소되었습니다.")

    flash(f"결제 실패: {error_msg}", "error")

    return redirect(url_for("store"))



@app.route("/gallery")
def gallery():
    posts = Post.query.filter(
        (Post.status == "approved") | (Post.status == None) | (Post.status == "")
    ).order_by(Post.is_free.desc(), Post.id.desc()).all()
    
    user_id = session.get("user_id")
    update_session_status(user_id)
    
    if user_id:
        try:
            user = User.query.get(user_id)
            session["is_seller"] = user.is_seller if user and hasattr(user, 'is_seller') else False
        except:
            session["is_seller"] = False
    
    categories = Category.query.filter_by(is_active=True).order_by(Category.sort_order).all()
    return render_template("gallery.html", posts=[p.to_dict() for p in posts], categories=categories)

@app.route("/community")
def community_page():
    posts = CommunityPost.query.order_by(CommunityPost.id.desc()).limit(50).all()
    my_linkreq_count = 0
    if session.get("user_email"):
        my_linkreq_count = LinkRequest.query.filter_by(requester_email=session["user_email"]).count()
    
    post_likes = {}
    for p in posts:
        post_likes[p.id] = CommunityLike.query.filter_by(post_id=p.id).count()
    
    return render_template("community.html", posts=posts, my_linkreq_count=my_linkreq_count, post_likes=post_likes, now=datetime.utcnow())

@app.route("/community/<int:post_id>")
def community_detail(post_id):
    post = CommunityPost.query.get_or_404(post_id)
    
    if not session.get("user_id") and not is_admin():
        if post.category not in ['free', 'revenue']:
            flash("로그인 후 이용 가능합니다.", "error")
            return redirect(url_for("login", next=f"/community/{post_id}"))
    
    comments = CommunityComment.query.filter_by(post_id=post_id).order_by(CommunityComment.created_at).all()
    like_count = CommunityLike.query.filter_by(post_id=post_id).count()
    user_liked = False
    if session.get("user_email"):
        user_liked = CommunityLike.query.filter_by(post_id=post_id, user_email=session.get("user_email")).first() is not None
    deal_apply_count = DealApplication.query.filter_by(post_id=post_id, status="approved").count() if post.category == "deal" else 0

    user_applied = DealApplication.query.filter_by(post_id=post_id, user_id=session.get("user_id")).first() is not None if session.get("user_id") and post.category == "deal" else False

    return render_template("community_detail.html", post=post, comments=comments, like_count=like_count, user_liked=user_liked, now=datetime.utcnow(), deal_apply_count=deal_apply_count, user_applied=user_applied)

@app.route("/community/<int:post_id>/delete", methods=["POST"])
def community_delete(post_id):
    if not session.get("user_id"):
        return redirect(url_for("login"))
    post = CommunityPost.query.get_or_404(post_id)
    if post.author_email != session.get("user_email"):
        abort(403)
    CommunityComment.query.filter_by(post_id=post_id).delete()
    CommunityLike.query.filter_by(post_id=post_id).delete()
    db.session.delete(post)
    db.session.commit()
    return redirect(url_for("community_page"))




@app.route("/community/<int:post_id>/edit", methods=["GET", "POST"])

def community_edit(post_id):

    if not session.get("user_id"):

        return redirect(url_for("login"))

    post = CommunityPost.query.get_or_404(post_id)

    user = User.query.get(session["user_id"])

    if post.author_email != user.email:

        return redirect(url_for("community_page"))

    if request.method == "POST":

        post.title = request.form.get("title", "").strip()

        post.content = request.form.get("content", "").strip()

        post.images_json = request.form.get("images_json", "[]")

        if post.category == "deal":

            post.deal_type = request.form.get("deal_type", "groupbuy")

            post.deal_max_people = request.form.get("deal_max_people", type=int)

            post.deal_subscribers_only = bool(request.form.get("deal_subscribers_only"))

            deadline = request.form.get("deal_deadline", "").strip()

            if deadline:

                try:

                    post.deal_deadline = datetime.strptime(deadline, "%Y-%m-%d %H:%M")

                except:

                    post.deal_deadline = None

            else:

                post.deal_deadline = None

        if post.title and post.content:

            db.session.commit()

            return '<script>location.replace("/community/' + str(post.id) + '")</script>'

    return render_template("community_edit.html", post=post, now=datetime.utcnow())

@app.route("/community/<int:post_id>/apply", methods=["POST"])

def community_deal_apply(post_id):

    if not session.get("user_id"):

        return jsonify({"ok": False, "error": "login_required"}), 401

    

    try:

        db.session.rollback()

    except:

        pass

    

    post = CommunityPost.query.get_or_404(post_id)

    

    if not post.is_deal_available:

        return jsonify({"ok": False, "error": "closed"})

    

    if post.deal_deadline and post.deal_deadline < datetime.utcnow():

        return jsonify({"ok": False, "error": "closed"})

    

    existing = DealApplication.query.filter_by(post_id=post_id, user_id=session["user_id"]).first()

    if existing:

        return jsonify({"ok": False, "error": "already_applied"})

    

    app_entry = DealApplication(

        post_id=post_id,

        user_id=session["user_id"],

        user_email=request.form.get("email", session.get("user_email", "")),

        name=request.form.get("name", ""),

        phone=request.form.get("phone", ""),

        sns_url=request.form.get("sns_url", ""),

        message=request.form.get("message", "")

    )

    db.session.add(app_entry)

    

    # 판매자(글 작성자)에게 알림

    author = User.query.filter_by(email=post.author_email).first()

    if author:

        applicant_name = request.form.get("name", "")

        noti = Notification(

            user_id=author.id,

            title="공구/협찬 신청",

            message=f"'{post.title}' 공구/협찬에 {applicant_name}님이 신청했습니다!"

        )

        db.session.add(noti)

    

    db.session.commit()

    

    return jsonify({"ok": True})



@app.route("/community/<int:post_id>/like", methods=["POST"])



def community_like(post_id):

    if not session.get("user_id") and not is_admin():

        return jsonify({"error": "login required"}), 401

    user_email = session.get("user_email")

    if not user_email:

        return jsonify({"error": "session expired, please re-login"}), 401

    existing = CommunityLike.query.filter_by(post_id=post_id, user_email=user_email).first()

    if existing:

        db.session.delete(existing)

        liked = False

    else:

        db.session.add(CommunityLike(post_id=post_id, user_email=user_email))

        liked = True

    db.session.commit()

    count = CommunityLike.query.filter_by(post_id=post_id).count()

    return jsonify({"liked": liked, "count": count})

@app.route("/community/<int:post_id>/comment", methods=["POST"])
def community_comment(post_id):
    if not session.get("user_id"):
        return redirect(url_for("login", next=f"/community/{post_id}"))
    content = (request.form.get("content") or "").strip()
    if not content:
        flash("댓글 내용을 입력하세요.", "error")
        return redirect(url_for("community_detail", post_id=post_id))
    db.session.add(CommunityComment(post_id=post_id, author_email=session.get("user_email"), content=content))
    db.session.commit()
    return redirect(url_for("community_detail", post_id=post_id))

@app.route("/community/comment/<int:comment_id>/delete", methods=["POST"])
def community_comment_delete(comment_id):
    if not session.get("user_id"):
        return redirect(url_for("login"))
    comment = CommunityComment.query.get_or_404(comment_id)
    if comment.author_email != session.get("user_email"):
        abort(403)
    post_id = comment.post_id
    db.session.delete(comment)
    db.session.commit()
    return redirect(url_for("community_detail", post_id=post_id))

@app.route("/my")
def my_page():
    if not session.get("user_id"):
        return redirect(url_for("login", next="/my"))
    if is_admin():
        return redirect(url_for("admin_posts"))

    user_email = session.get("user_email", "")
    user_id = session.get("user_id")
    
    update_session_status(user_id)
    
    user_subscriptions = get_user_subscriptions(user_id)
    
    link_request_count = LinkRequest.query.filter_by(requester_email=user_email).count()
    link_request_done = LinkRequest.query.filter(
        LinkRequest.requester_email == user_email,
        LinkRequest.coupang_url != None,
        LinkRequest.coupang_url != ""
    ).count()
    
    monthly_used = get_monthly_link_request_count(user_email)
    monthly_limit = get_link_request_limit(user_id)
    
    trial_expires_at = get_trial_expires_at(user_id)
    can_trial = can_use_free_trial(user_id)
    
    user = User.query.get(user_id)
    user_is_seller = user.is_seller if user else False
    user_seller_status = user.seller_status if user else None
    user_seller_company = user.seller_company if user else None
    user_seller_category = user.seller_category if user else None

    if not user.referral_code:
        import random
        import string
        user.referral_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        db.session.commit()
    
    invited_count = User.query.filter_by(referred_by=user.id).count()
    my_posts_count = CommunityPost.query.filter_by(author_email=user_email).count()
    
    return render_template("my.html",
        user_email=user_email,
        subscriptions=user_subscriptions,
        link_request_count=link_request_count,
        link_request_done=link_request_done,
        monthly_used=monthly_used,
        monthly_limit=monthly_limit,
        trial_expires_at=trial_expires_at,
        can_use_trial=can_trial,
        now=datetime.utcnow(),
        user_is_seller=user_is_seller,
        user_seller_status=user_seller_status,
        user_seller_company=user_seller_company,
        user_seller_category=user_seller_category,
        referral_code=user.referral_code,
        invited_count=invited_count,
        my_posts_count=my_posts_count,
        profile_photo=user.profile_photo if user else ""
    )

@app.route("/profitguard")
def profitguard_page():
    pg_plan = None
    if session.get("user_id"):
        pg_plan = "none"
        if can_access_profitguard(session["user_id"]):
            sub = Subscription.query.filter(
                Subscription.user_id == session["user_id"],
                Subscription.status == "active",
                Subscription.plan_type.in_(["profitguard_pro", "profitguard_lite", "profitguard_lifetime", "allinone"])
            ).first()
            if sub:
                pg_plan = sub.plan_type
    is_kakao = False

    pg_pw_set = False

    if session.get("user_id"):

        u = db.session.get(User, session["user_id"])

        if u and u.kakao_id:

            is_kakao = True

            pg_pw_set = bool(u.pg_password_set)

    return render_template("profitguard.html", pg_plan=pg_plan, is_kakao=is_kakao, pg_pw_set=pg_pw_set)

@app.route("/proof")
@cache.cached(timeout=3600)
def proof_page():
    return render_template("proof.html")

@app.route("/subscribe-info")
@cache.cached(timeout=3600)
def subscribe_info():
    return render_template("subscribe.html")

@app.route("/terms")
@cache.cached(timeout=86400)
def terms():
    return render_template("terms.html")

@app.route("/privacy")
@cache.cached(timeout=86400)
def privacy():
    return render_template("privacy.html")

@app.route("/refund")
@cache.cached(timeout=86400)
def refund():
    return render_template("refund.html")


# ----------------------------
# 무료 체험 신청
# ----------------------------
@app.route("/free-trial", methods=["GET", "POST"])
def free_trial():
    if not session.get("user_id"):
        return redirect(url_for("login", next="/free-trial"))
    
    user = User.query.get(session.get("user_id"))
    if not user:
        return redirect(url_for("login", next="/free-trial"))
    
    if user.free_trial_used:
        flash("이미 무료 체험을 사용하셨습니다.", "error")
        return redirect(url_for("pricing"))
    
    
    # 구독 이력 있으면 체험 차단 (해지 후 재신청 방지)
    all_subs = Subscription.query.filter_by(user_id=user.id).all()
    if all_subs:
        flash("이미 구독 이력이 있어 무료 체험을 사용할 수 없습니다.", "error")
        return redirect(url_for("pricing"))
    if get_user_subscriptions(user.id):
        flash("이미 구독 중이십니다.", "error")
        return redirect(url_for("gallery"))
    
    if request.method == "POST":
        user.free_trial_used = True
        user.free_trial_expires = datetime.utcnow() + timedelta(days=5)
        db.session.commit()
        
        session["is_trial"] = True
        session["subscriber"] = True
        
        flash("🎉 5일 무료 체험이 시작되었습니다!", "success")
        return redirect(url_for("gallery"))
    
    return render_template("free_trial.html")


@app.route("/community/write", methods=["GET", "POST"])
def community_write():
    if not session.get("user_id"):
        return redirect(url_for("login", next="/community/write"))
    
    user = User.query.get(session.get("user_id"))
    if user:
        session["is_seller"] = user.is_seller
    
    if request.method == "POST":
        category = (request.form.get("category") or "free").strip()
        title = (request.form.get("title") or "").strip()
        content = (request.form.get("content") or "").strip()
        images = parse_json_list_field("images_json")
        
        if category == "deal" and not (session.get("is_seller") or is_admin()):
            flash("공구/협찬 글은 판매자만 작성할 수 있습니다.", "error")
            return redirect(url_for("community_write"))
        
        if not title or not content:
            flash("제목/내용을 입력해주세요.", "error")
            return redirect(url_for("community_write"))

        post = CommunityPost(

            category=category, title=title, content=content,

            author_email=session.get("user_email", "guest"),

            images_json=json.dumps(images, ensure_ascii=False)

        )

        if category == "deal":

            post.deal_type = request.form.get("deal_type", "groupbuy")

            post.deal_max_people = request.form.get("deal_max_people", type=int)

            post.deal_subscribers_only = bool(request.form.get("deal_subscribers_only"))

            deadline = request.form.get("deal_deadline", "").strip()

            if deadline:

                try:

                    post.deal_deadline = datetime.strptime(deadline, "%Y-%m-%d")

                except:

                    try:

                        post.deal_deadline = datetime.strptime(deadline, "%Y-%m-%d %H:%M")

                    except:

                        post.deal_deadline = None

        db.session.add(post)

        db.session.commit()

        return redirect(url_for("community_page"))

    return render_template("community_write.html")

    return render_template("community_write.html")


# ----------------------------
# Link Request Routes
# ----------------------------
@app.route("/link-requests/new", methods=["GET", "POST"])
def link_request_new():
    if not session.get("user_id"):
        return redirect(url_for("login", next="/link-requests/new"))

    user_id = session.get("user_id")
    user_email = session.get("user_email", "")
    
    update_session_status(user_id)
    
    monthly_used = get_monthly_link_request_count(user_email)
    monthly_limit = get_link_request_limit(user_id)

    if request.method == "POST":
        if not can_make_link_request(user_id, user_email):
            flash(f"이번 달 링크요청 한도({monthly_limit}회)를 초과했습니다.", "error")
            return redirect(url_for("link_requests"))

        kakao_nickname = (request.form.get("kakao_nickname") or "").strip()
        kakao_password = (request.form.get("kakao_password") or "").strip()
        title = (request.form.get("title") or "").strip()
        original_url = (request.form.get("original_url") or "").strip()

        OPENROOM_PASSWORD = "머닝화이팅"

        is_sub_or_trial = session.get("subscriber") or session.get("is_trial")
        if not is_sub_or_trial:
            if not kakao_nickname or not kakao_password:
                flash("오픈방 닉네임과 인증 암호를 입력해주세요.", "error")
                return redirect(url_for("link_request_new"))
            if kakao_password != OPENROOM_PASSWORD:
                flash("오픈방 인증 암호가 틀렸습니다.", "error")
                return redirect(url_for("link_request_new"))

        if not title or not original_url:
            flash("제목과 원본 링크를 입력해주세요.", "error")
            return redirect(url_for("link_request_new"))

        db.session.add(LinkRequest(
            title=title, original_url=original_url,
            requester_email=user_email, kakao_nickname=kakao_nickname
        ))
        db.session.commit()
        return redirect(url_for("my_link_requests", success=1))

    user_email = session.get("user_email", "")

    items = LinkRequest.query.filter_by(requester_email=user_email).order_by(LinkRequest.id.desc()).limit(10).all() if user_email else []

    return render_template("link_request_new.html", monthly_used=monthly_used, monthly_limit=monthly_limit, items=items)

@app.route("/link-requests")
def link_requests():
    user_id = session.get("user_id")
    user_email = session.get("user_email", "")

    if user_id:
        update_session_status(user_id)

    if is_admin():
        items = LinkRequest.query.order_by(LinkRequest.id.desc()).all()
        monthly_used, monthly_limit = 0, 999
    elif user_id:
        items = LinkRequest.query.filter_by(requester_email=user_email).order_by(LinkRequest.id.desc()).all()
        monthly_used = get_monthly_link_request_count(user_email)
        monthly_limit = get_link_request_limit(user_id)
    else:
        items = []
        monthly_used, monthly_limit = 0, 3

    return render_template("link_requests.html", items=items, monthly_used=monthly_used, monthly_limit=monthly_limit)

@app.route("/link-requests/<int:request_id>", methods=["GET", "POST"])
def link_request_detail(request_id):
    it = LinkRequest.query.get_or_404(request_id)
    if not is_admin():
        if not session.get("user_id"):
            return redirect(url_for("login", next=f"/link-requests/{request_id}"))
        if it.requester_email != session.get("user_email", ""):
            abort(403)
    if request.method == "POST":
        if not is_admin():
            abort(403)
        it.coupang_url = (request.form.get("coupang_url") or "").strip()
        db.session.commit()
        
        if it.coupang_url:
            user = User.query.filter_by(email=it.requester_email).first()
            if user:
                noti = Notification(
                    user_id=user.id,
                    type="link_completed",
                    title="링크요청 완료",
                    message=f"'{it.title}' 요청의 쿠팡 링크가 등록되었습니다!",
                    link="/link-requests/new"
                )
                db.session.add(noti)
                db.session.commit()
        
        return redirect(url_for("admin_link_requests"))
    return render_template("link_request_detail.html", it=it)


# ----------------------------
# Auth
# ----------------------------
@app.route("/register", methods=["GET", "POST"])
@app.route("/signup", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = (request.form.get("password") or "").strip()
        referral_code = (request.form.get("referral_code") or "").strip().upper()
        
        if not email or not password:
            flash("이메일/비밀번호를 입력하세요.", "error")
        password_confirm = (request.form.get("password_confirm") or "").strip()
        if password != password_confirm:
            flash("비밀번호가 일치하지 않습니다.", "error")
            return redirect(url_for("register"))
            return redirect(url_for("register"))
        if User.query.filter_by(email=email).first():
            flash("이미 가입된 이메일입니다.", "error")
            return redirect(url_for("register"))
        
        referred_by_id = None
        if referral_code:
            referrer = User.query.filter_by(referral_code=referral_code).first()
            if referrer:
                referred_by_id = referrer.id
        
        phone = (request.form.get("phone") or "").strip().replace("-", "")
        u = User(email=email, pw_hash=generate_password_hash(password), referred_by=referred_by_id, phone=phone)
        db.session.add(u)
        db.session.commit()
        
        if referred_by_id:
            referrer = User.query.get(referred_by_id)
            if referrer:
                active_sub = Subscription.query.filter(
                    Subscription.user_id == referrer.id,
                    Subscription.expires_at > datetime.utcnow()
                ).order_by(Subscription.expires_at.desc()).first()
                
                if active_sub:
                    active_sub.expires_at = active_sub.expires_at + timedelta(days=7)
                    db.session.commit()
        
        session.clear()
        session["user_id"] = u.id
        session["user_email"] = u.email
        session["subscriber"] = False
        session["is_trial"] = False
        try:
            if u.phone:
                send_welcome_alimtalk(u.phone)
        except Exception as e:
            print(f"[알림톡] 가입환영 발송 오류: {e}")
        return redirect(url_for("index"))



    return render_template("register.html")


@app.route("/auth/kakao")
def kakao_login():
    next_url = request.args.get("next", "")
    state = ""
    if next_url:
        import base64
        state = base64.urlsafe_b64encode(next_url.encode()).decode()
    kakao_auth_url = f"https://kauth.kakao.com/oauth/authorize?client_id={KAKAO_REST_API_KEY}&redirect_uri={KAKAO_REDIRECT_URI}&response_type=code"
    if state:
        kakao_auth_url += f"&state={state}"
    return redirect(kakao_auth_url)

@app.route("/auth/kakao/callback")
def kakao_callback():
    code = request.args.get("code")
    if not code:
        return "에러: code 없음", 400
    
    token_url = "https://kauth.kakao.com/oauth/token"
    token_data = {
        "grant_type": "authorization_code",
        "client_id": KAKAO_REST_API_KEY,
        "redirect_uri": KAKAO_REDIRECT_URI,
        "code": code
    }
    token_response = requests.post(token_url, data=token_data)
    token_json = token_response.json()
    
    access_token = token_json.get("access_token")
    
    user_info_url = "https://kapi.kakao.com/v2/user/me"
    headers = {"Authorization": f"Bearer {access_token}"}
    user_response = requests.post(user_info_url, headers={

        "Authorization": f"Bearer {access_token}",

        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8"

    }, data={

        "property_keys": json.dumps(["kakao_account.email", "kakao_account.phone_number", "kakao_account.profile"])

    })
    user_json = user_response.json()
    
    kakao_id = str(user_json.get("id"))
    kakao_account = user_json.get("kakao_account", {})
    email = kakao_account.get("email")
    nickname = kakao_account.get("profile", {}).get("nickname", "")
    phone_number = kakao_account.get("phone_number", "")
    profile_image = kakao_account.get("profile", {}).get("profile_image_url", "")
    if phone_number:
        phone_number = phone_number.replace("+82 ", "0").replace("-", "").replace(" ", "")
    
    if not email:
        email = f"kakao_{kakao_id}@moneying.biz"
    
    user = User.query.filter_by(email=email).first()
    is_new_user = user is None
    if not user:
        user = User(
            email=email,
            pw_hash=secrets.token_hex(16),
            kakao_id=kakao_id
        )
        db.session.add(user)
        db.session.commit()
    elif not user.kakao_id:
        user.kakao_id = kakao_id
        db.session.commit()
    # 전화번호/프사 업데이트 (비어있으면 채우기)
    if phone_number and not user.phone:
        user.phone = phone_number
    if profile_image and not user.profile_photo:
        user.profile_photo = profile_image
    db.session.commit()
    
    new_token = secrets.token_hex(32)


    # 카카오 신규가입 알림톡

    if is_new_user:

        try:

            if user.phone:

                send_welcome_alimtalk(user.phone)

        except Exception as e:

            print(f"[알림톡] 카카오 가입환영 발송 오류: {e}")



    user.session_token = new_token
    db.session.commit()
    
    session.clear()

    session["user_id"] = user.id

    session["user_email"] = user.email

    session["session_token"] = new_token

    update_session_status(user.id)

    

    state = request.args.get("state", "")

    if state:

        try:

            import base64

            next_url = base64.urlsafe_b64decode(state.encode()).decode()

            if next_url == "profitguard-event-apply":

                try:

                    _apply_profitguard_event(user)

                except Exception as e:

                    print(f"[PG EVENT] auto-apply error: {e}")

                return redirect("/profitguard-event?applied=1")

            if next_url.startswith("/"):

                return redirect(next_url)

        except Exception as e:

            print(f"[KAKAO] state decode error: {e}")

    

    return redirect(url_for("index"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if is_admin():
        return redirect(url_for("admin_home"))
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = (request.form.get("password") or "").strip()
        next_url = (request.form.get("next") or "").strip()
        
        if email == ADMIN_EMAIL and password == ADMIN_PASSWORD:
            session.clear()
            session["admin"] = True
            _au = User.query.filter_by(email=ADMIN_EMAIL).first()
            if _au:
                session["user_id"] = _au.id
                session["user_email"] = _au.email
            return redirect(url_for("admin_home"))
        
        u = User.query.filter_by(email=email).first()
        
        if u and u.locked_until:
            if datetime.now() < u.locked_until:
                remaining = (u.locked_until - datetime.now()).seconds // 60 + 1
                flash(f"로그인 시도 5회 실패로 계정이 잠겼습니다. {remaining}분 후 다시 시도해주세요.", "error")
                return redirect(url_for("login"))
            else:
                u.locked_until = None
                u.login_fail_count = 0
                db.session.commit()
        
        if not u or not check_password_hash(u.pw_hash, password):
            if u:
                u.login_fail_count = (u.login_fail_count or 0) + 1
                if u.login_fail_count >= 5:
                    u.locked_until = datetime.now() + timedelta(minutes=30)
                    db.session.commit()
                    flash("로그인 5회 실패로 계정이 30분간 잠겼습니다.", "error")
                    return redirect(url_for("login"))
                db.session.commit()
                flash(f"로그인 정보가 올바르지 않습니다. ({u.login_fail_count}/5회 실패)", "error")
            else:
                flash("로그인 정보가 올바르지 않습니다.", "error")
            return redirect(url_for("login"))
        
        u.login_fail_count = 0
        u.locked_until = None
        db.session.commit()
        
        new_token = secrets.token_hex(32)
        u.session_token = new_token
        db.session.commit()
        
        session.clear()
        session["user_id"] = u.id
        session["user_email"] = u.email
        session["session_token"] = new_token
        
        update_session_status(u.id)
        
        if next_url and next_url.startswith("/"):
            return redirect(next_url)
        return redirect(url_for("index"))
    return render_template("login.html", next_url=request.args.get("next", ""))

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        user = User.query.filter_by(email=email).first()
        
        if not user:
            return render_template("forgot_password.html", error="등록되지 않은 이메일입니다.")
        
        import random
        import string
        temp_pw = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        user.pw_hash = generate_password_hash(temp_pw)
        db.session.commit()
        
        html_body = f"""
        <div style="font-family: 'Noto Sans KR', Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 40px 20px; background: #0a0a0a; color: #fff;">
            <div style="text-align: center; margin-bottom: 40px;">
                <h1 style="font-size: 28px; font-weight: 900; margin: 0;">
                    MONEY<span style="color: #a3e635;">ING</span>
                </h1>
            </div>
            
            <div style="background: #18181b; border: 1px solid #27272a; border-radius: 16px; padding: 32px;">
                <h2 style="font-size: 20px; font-weight: 700; margin: 0 0 16px 0;">임시 비밀번호 안내</h2>
                <p style="color: #a1a1aa; margin: 0 0 24px 0; line-height: 1.6;">
                    요청하신 임시 비밀번호입니다.<br>
                    로그인 후 반드시 비밀번호를 변경해주세요.
                </p>
                
                <div style="background: #27272a; border-radius: 12px; padding: 20px; text-align: center; margin-bottom: 24px;">
                    <p style="color: #71717a; font-size: 12px; margin: 0 0 8px 0;">임시 비밀번호</p>
                    <p style="font-size: 28px; font-weight: 900; color: #a3e635; margin: 0; letter-spacing: 2px;">{temp_pw}</p>
                </div>
                
                <a href="https://moneying.co.kr/login" 
                   style="display: block; background: #a3e635; color: #000; text-decoration: none; text-align: center; padding: 16px; border-radius: 12px; font-weight: 700;">
                    로그인하기 →
                </a>
            </div>
            
            <p style="color: #52525b; font-size: 12px; text-align: center; margin-top: 32px;">
                본인이 요청하지 않은 경우, 이 이메일을 무시해주세요.
            </p>
        </div>
        """
        
        email_sent = send_email(email, "[MONEYING] 임시 비밀번호 안내", html_body)
        
        if email_sent:
            return render_template("forgot_password.html", success=True)
        else:
            return render_template("forgot_password.html", temp_password=temp_pw, email_failed=True)
    
    return render_template("forgot_password.html")

@app.route("/change-password", methods=["GET", "POST"])
def change_password():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    
    user = User.query.get(session["user_id"])
    if not user:
        return redirect(url_for("login"))
    
    if request.method == "POST":
        current_pw = (request.form.get("current_password") or "").strip()
        new_pw = (request.form.get("new_password") or "").strip()
        confirm_pw = (request.form.get("confirm_password") or "").strip()
        
        if not check_password_hash(user.pw_hash, current_pw):
            flash("현재 비밀번호가 올바르지 않습니다.", "error")
            return redirect(url_for("change_password"))
        
        if len(new_pw) < 6:
            flash("새 비밀번호는 6자 이상이어야 합니다.", "error")
            return redirect(url_for("change_password"))
        
        if new_pw != confirm_pw:
            flash("새 비밀번호가 일치하지 않습니다.", "error")
            return redirect(url_for("change_password"))
        
        user.pw_hash = generate_password_hash(new_pw)
        db.session.commit()
        
        flash("비밀번호가 변경되었습니다.", "success")
        return redirect(url_for("my_page"))
    
    return render_template("change_password.html")

@app.route("/logout")
def logout():
    session.clear()
    response = redirect(url_for("index"))
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# ----------------------------
# Admin Auth
# ----------------------------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if is_admin():
        return redirect(url_for("admin_home"))
    if request.method == "POST":
        if (request.form.get("password") or "").strip() == ADMIN_PASSWORD:
            session.clear()
            session["admin"] = True
            _au = User.query.filter_by(email=ADMIN_EMAIL).first()
            if _au:
                session["user_id"] = _au.id
                session["user_email"] = _au.email
            return redirect(url_for("admin_home"))
        flash("비밀번호가 틀렸습니다.", "error")
        return redirect(url_for("admin_login"))
    return render_template("admin_login.html")

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("index"))


# ----------------------------
# Admin Home
# ----------------------------
@app.route("/admin")
def admin_home():
    if not is_admin():
        return redirect(url_for("admin_login"))
    
    from datetime import date
    today = date.today()
    
    try:
        today_users = User.query.filter(db.func.date(User.created_at) == today).count()
    except:
        today_users = 0
    
    try:
        today_links = LinkRequest.query.filter(db.func.date(LinkRequest.created_at) == today).count()
    except:
        today_links = 0
    
    pending_links = LinkRequest.query.filter((LinkRequest.coupang_url == None) | (LinkRequest.coupang_url == "")).count()
    
    try:
        pending_sellers = User.query.filter_by(seller_status="pending").count()
    except:
        pending_sellers = 0
    
    # [FIX #3] RevenueProof → RevenueRewardHistory 수정
    try:
        pending_rewards = RevenueRewardHistory.query.filter_by(status="pending").count()
    except:
        pending_rewards = 0
    
    pending_posts = Post.query.filter_by(status="pending").count()
    
    return render_template("admin_home.html",
        today=today,
        today_users=today_users,
        today_links=today_links,
        
        pending_links=pending_links,
        pending_sellers=pending_sellers,
        pending_rewards=pending_rewards,
        pending_posts=pending_posts,
        pg_event_count=EventTrialApply.query.count() if EventTrialApply else 0,
        
        user_count=User.query.count(),
        subscriber_count=db.session.query(Subscription.user_id).filter_by(status="active").distinct().count(),
        gallery_count=Post.query.count(),
        store_count=StoreProduct.query.count(),
        
        recent_link_requests=LinkRequest.query.order_by(desc(LinkRequest.id)).limit(5).all(),
        recent_posts=Post.query.order_by(desc(Post.id)).limit(5).all()
    )

@app.route("/admin/pending-posts")



@app.route("/admin/event-trials")

def admin_event_trials():

    if not is_admin():

        return redirect(url_for("admin_login"))

    trials = EventTrialApply.query.order_by(desc(EventTrialApply.created_at)).all()

    return render_template("admin_event_trials.html", trials=trials, total=len(trials))



def admin_pending_posts():
    if not is_admin():
        return redirect(url_for("admin_login"))
    
    posts = Post.query.filter_by(status="pending").order_by(desc(Post.id)).all()
    return render_template("admin_pending_posts.html", posts=posts)


@app.route("/admin/pending-posts/<int:post_id>/approve", methods=["POST"])
def admin_approve_post(post_id):
    if not is_admin():
        return jsonify({"ok": False, "error": "권한 없음"})
    
    post = Post.query.get_or_404(post_id)
    post.status = "approved"
    db.session.commit()
    
    if post.seller_id:
        noti = Notification(
            user_id=post.seller_id,
            type="post_approved",
            title="게시물 승인 완료",
            message=f"'{post.title}' 게시물이 승인되어 갤러리에 노출됩니다.",
            link="/gallery"
        )
        db.session.add(noti)
        db.session.commit()
    
    return jsonify({"ok": True})


@app.route("/admin/pending-posts/<int:post_id>/reject", methods=["POST"])
def admin_reject_post(post_id):
    if not is_admin():
        return jsonify({"ok": False, "error": "권한 없음"})
    
    post = Post.query.get_or_404(post_id)
    post.status = "rejected"
    db.session.commit()
    
    if post.seller_id:
        noti = Notification(
            user_id=post.seller_id,
            type="post_rejected",
            title="게시물 반려",
            message=f"'{post.title}' 게시물이 반려되었습니다. 내용을 확인해주세요.",
            link="/seller/dashboard"
        )
        db.session.add(noti)
        db.session.commit()
    
    return jsonify({"ok": True})


@app.route("/admin/users")
def admin_users():
    if not is_admin():
        return redirect(url_for("admin_login"))
    
    users = User.query.order_by(desc(User.id)).all()
    
    now = datetime.utcnow()
    subscriber_count = 0
    trial_count = 0
    
    for user in users:
        active_sub = Subscription.query.filter(
            Subscription.user_id == user.id,
            Subscription.status == "active",
            (Subscription.expires_at == None) | (Subscription.expires_at > now)
        ).first()
        user.subscriber = active_sub is not None
        
        user.is_trial = user.free_trial_expires and user.free_trial_expires > now
        
        if user.subscriber:
            subscriber_count += 1
        elif user.is_trial:
            trial_count += 1
    
    return render_template("admin_users.html", 
        users=users, 
        subscriber_count=subscriber_count,
        trial_count=trial_count,
        now=now
    )

@app.route("/admin/stats")
def admin_stats():
    if not is_admin():
        return redirect(url_for("admin_login"))
    
    from datetime import date, timedelta as td
    today = date.today()
    
    total_users = User.query.count()
    new_users_today = User.query.filter(db.func.date(User.created_at) == today).count()
    total_subscribers = db.session.query(Subscription.user_id).filter_by(status="active").distinct().count()
    trial_users = User.query.filter(User.free_trial_expires > datetime.utcnow()).count()
    total_posts = Post.query.count()
    
    conversion_rate = round((total_subscribers / total_users * 100), 1) if total_users > 0 else 0
    
    daily_signups = []
    max_daily_signup = 0
    for i in range(6, -1, -1):
        d = today - td(days=i)
        count = User.query.filter(db.func.date(User.created_at) == d).count()
        daily_signups.append({"label": d.strftime("%m/%d"), "count": count})
        if count > max_daily_signup:
            max_daily_signup = count
    
    popular_posts = Post.query.order_by(Post.view_count.desc()).limit(5).all()
    recent_users = User.query.order_by(User.created_at.desc()).limit(5).all()
    
    return render_template("admin_stats.html",
        total_users=total_users,
        new_users_today=new_users_today,
        total_subscribers=total_subscribers,
        conversion_rate=conversion_rate,
        trial_users=trial_users,
        total_posts=total_posts,
        daily_signups=daily_signups,
        max_daily_signup=max_daily_signup,
        popular_posts=popular_posts,
        recent_users=recent_users,
        gallery_count=Post.query.count(),
        link_count=LinkRequest.query.count(),
        user_count=User.query.count(),
        store_count=StoreProduct.query.count(),
        subscriber_count=total_subscribers
    )

@app.route("/api/gallery/<int:post_id>/view", methods=["POST"])
def api_gallery_view(post_id):
    post = Post.query.get_or_404(post_id)
    post.view_count = (post.view_count or 0) + 1
    db.session.commit()
    return jsonify({"ok": True, "view_count": post.view_count})


# ----------------------------
# Admin Gallery
# ----------------------------
@app.route("/admin/gallery")
def admin_gallery():
    if not is_admin():
        return redirect(url_for("admin_login"))
    posts = Post.query.filter(Post.is_deleted != True).order_by(Post.id.desc()).all()

    uploaders = {}

    for p in posts:

        if p.uploaded_by and p.uploaded_by not in uploaders:

            u = db.session.get(User, p.uploaded_by)

            uploaders[p.uploaded_by] = (u.nickname or u.email) if u else None

        if p.seller_id and p.seller_id not in uploaders:

            u = db.session.get(User, p.seller_id)

            uploaders[p.seller_id] = (u.nickname or u.email) if u else None

    categories = Category.query.filter(

        Category.is_active == True,

        Category.key.notin_(['all', 'bookmark', 'recent'])

    ).order_by(Category.sort_order).all()

    return render_template("admin_gallery.html", posts=posts, uploaders=uploaders, categories=categories)


@app.route("/admin/gallery/trash")

def admin_gallery_trash():

    if not is_admin():

        return redirect(url_for("admin_login"))

    posts = Post.query.filter_by(is_deleted=True).order_by(Post.id.desc()).all()

    return render_template("admin_gallery_trash.html", posts=posts)



@app.route("/admin/gallery/restore/<int:post_id>", methods=["POST"])

def admin_gallery_restore(post_id):

    if not is_admin():

        return redirect(url_for("admin_login"))

    post = Post.query.get_or_404(post_id)

    post.is_deleted = False

    db.session.commit()

    flash("게시물이 복원되었습니다.", "success")

    return redirect(url_for("admin_gallery_trash"))



@app.route("/admin/gallery/restore-all", methods=["POST"])

def admin_gallery_restore_all():

    if not is_admin():

        return redirect(url_for("admin_login"))

    Post.query.filter_by(is_deleted=True).update({"is_deleted": False})

    db.session.commit()

    flash("\uc804\uccb4 \ubcf5\uc6d0\ub418\uc5c8\uc2b5\ub2c8\ub2e4.", "success")

    return redirect(url_for("admin_gallery"))



@app.route("/admin/gallery/restore-selected", methods=["POST"])

def admin_gallery_restore_selected():

    if not is_admin():

        return jsonify({"error": "unauthorized"}), 403

    data = request.get_json() or {}

    ids = data.get("ids", [])

    if ids:

        Post.query.filter(Post.id.in_([int(i) for i in ids])).update({"is_deleted": False}, synchronize_session=False)

        db.session.commit()

    return jsonify({"ok": True})



@app.route("/admin/gallery/toggle-featured/<int:post_id>", methods=["POST"])

def admin_toggle_featured(post_id):

    if not is_admin():

        return jsonify({"error": "unauthorized"}), 403

    post = Post.query.get_or_404(post_id)

    if not post.is_featured:

        featured_count = Post.query.filter_by(is_featured=True).count()

        if featured_count >= 4:

            return jsonify({"error": "max", "msg": "\uba54\uc778 \ucd94\ucc9c\uc740 \ucd5c\ub300 4\uac1c\uc785\ub2c8\ub2e4."}), 400

    post.is_featured = not post.is_featured

    db.session.commit()

    return jsonify({"ok": True, "featured": post.is_featured})



@app.route("/admin/gallery/bulk")
def admin_gallery_bulk():
    if not is_admin():
        return redirect(url_for("admin_login"))
    categories = Category.query.filter(
        Category.is_active == True,
        Category.key.notin_(['all', 'bookmark', 'recent'])
    ).order_by(Category.sort_order).all()
    return render_template("admin_gallery_bulk.html", categories=categories)

@app.route("/admin/gallery/bulk/sample")
def admin_gallery_bulk_sample():
    if not is_admin():
        return redirect(url_for("admin_login"))
    csv_content = "\ufeff"
    csv_content += "title,category,video_url1,video_url2,video_url3,coupang_url,is_free\n"
    csv_content += "미니선풍기,living,https://tiktok.com/...,https://instagram.com/...,,https://coupang.com/...,0\n"
    csv_content += "다이어트음료,food,https://tiktok.com/...,,https://xiaohongshu.com/...,https://coupang.com/...,1\n"
    from flask import Response
    return Response(
        csv_content,
        mimetype="text/csv; charset=utf-8-sig",
        headers={"Content-disposition": "attachment; filename=sample_bulk_upload.csv"}
    )

@app.route("/admin/gallery/bulk/upload", methods=["POST"])
def admin_gallery_bulk_upload():
    if not is_admin():
        return jsonify({"error": "unauthorized"}), 401
    
    import csv
    import io
    
    file = request.files.get("csv_file")
    if not file:
        return jsonify({"error": "파일이 없습니다"}), 400
    
    try:
        raw_data = file.read()
        content = None
        
        for encoding in ["utf-8-sig", "utf-8", "cp949", "euc-kr"]:
            try:
                content = raw_data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        
        if content is None:
            return jsonify({"error": "파일 인코딩을 인식할 수 없습니다"}), 400
        
        lines = content.strip().split('\n')
        delimiter = '\t' if '\t' in lines[0] else ','
        
        header_line = lines[0]
        headers = [h.strip().lower() for h in header_line.split(delimiter)]
        
        count = 0
        for line in lines[1:]:
            if not line.strip():
                continue
            
            values = line.split(delimiter)
            row = {}
            for i, header in enumerate(headers):
                if i < len(values):
                    row[header] = values[i].strip()
                else:
                    row[header] = ""
            
            title = row.get("title", "").strip()
            if not title:
                continue
            
            post = Post(
                title=title,
                category=row.get("category", "all").strip().lower(),
                video_url=row.get("video_url1", "").strip(),
                video_url2=row.get("video_url2", "").strip(),
                video_url3=row.get("video_url3", "").strip(),
                coupang_link=row.get("coupang_url", "").strip(),
                is_free=row.get("is_free", "0").strip() == "1",
                uploaded_by=session.get("user_id")
            )
            db.session.add(post)
            count += 1
        
        db.session.commit()
        return jsonify({"ok": True, "count": count})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/admin/posts")
def admin_posts():
    if not is_admin():
        return redirect(url_for("admin_login"))
    return redirect("/admin/gallery")


@app.route("/admin/gallery/bulk-delete", methods=["POST"])
def admin_gallery_bulk_delete():
    if not is_admin():
        return jsonify({"error": "unauthorized"}), 401
    
    data = request.get_json()
    ids = data.get("ids", [])
    
    if not ids:
        return jsonify({"error": "삭제할 항목이 없습니다"}), 400
    
    try:
        count = Post.query.filter(Post.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()
        return jsonify({"ok": True, "count": count})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400

@app.route("/admin/upload")
def admin_upload():
    if not is_admin():
        return redirect(url_for("admin_login"))
    categories = Category.query.filter(
        Category.is_active == True,
        Category.key.notin_(['all', 'bookmark', 'recent'])
    ).order_by(Category.sort_order).all()
    return render_template("admin_upload.html", categories=categories)

def parse_json_list_field(field_name: str):
    raw = (request.form.get(field_name) or "[]").strip()
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else []
    except Exception:
        return []


@app.route("/admin/posts/<int:post_id>/edit", methods=["GET", "POST"])
def admin_edit(post_id):
    if not is_admin():
        return redirect(url_for("admin_login"))
    p = Post.query.get_or_404(post_id)
    next_url = request.args.get("next") or url_for("admin_gallery")
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        if not title:
            return jsonify({"ok": False, "error": "title_required"}), 400
        images = parse_json_list_field("images_json")
        if not images:
            return jsonify({"ok": False, "error": "images_required"}), 400
        p.title = title
        p.category = (request.form.get("category") or "all").strip()
        p.coupang_link = (request.form.get("coupang_link") or "").strip()
        p.images_json = json.dumps(images, ensure_ascii=False)
        p.links_json = json.dumps(parse_json_list_field("links_json"), ensure_ascii=False)
        p.tags_json = json.dumps(parse_json_list_field("tags_json"), ensure_ascii=False)
        p.is_free = request.form.get("is_free") == "on"
        db.session.commit()
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": True, "redirect": next_url})
        return redirect(next_url)
    categories = Category.query.filter(Category.key.notin_(["all","bookmark","recent","popular"])).filter_by(is_active=True).order_by(Category.sort_order).all()
    return render_template("admin_edit.html", post=p, post_data=p.to_dict(), next_url=next_url, categories=categories)

@app.route("/admin/posts/<int:post_id>/delete", methods=["POST"])

def admin_delete(post_id):

    if not is_admin():

        return redirect(url_for("admin_login"))

    post = Post.query.get_or_404(post_id)

    post.is_deleted = True

    db.session.commit()

    flash("게시물이 삭제되었습니다. (복원 가능)", "success")

    return redirect(request.args.get("next") or url_for("admin_gallery"))
    db.session.delete(Post.query.get_or_404(post_id))
    db.session.commit()
    return redirect(url_for("admin_posts"))


# ----------------------------
# Admin Link Requests
# ----------------------------
@app.route("/admin/link-requests")
def admin_link_requests():
    if not is_admin():
        return redirect(url_for("admin_login"))
    return render_template("admin_link_requests.html", items=LinkRequest.query.order_by(LinkRequest.id.desc()).all())


# ----------------------------
# Admin Categories
# ----------------------------
@app.route("/admin/categories")
def admin_categories():
    if not is_admin():
        return redirect(url_for("admin_login"))
    categories = Category.query.order_by(Category.sort_order).all()
    return render_template("admin_categories.html", categories=categories)

@app.route("/admin/categories/add", methods=["POST"])
def admin_category_add():
    if not is_admin():
        return redirect(url_for("admin_login"))
    
    key = request.form.get("key", "").strip().lower()
    name = request.form.get("name", "").strip()
    emoji = request.form.get("emoji", "").strip()
    
    if not key or not name:
        flash("키와 이름은 필수입니다.")
        return redirect(url_for("admin_categories"))
    
    existing = Category.query.filter_by(key=key).first()
    if existing:
        flash("이미 존재하는 카테고리 키입니다.")
        return redirect(url_for("admin_categories"))
    
    max_order = db.session.query(db.func.max(Category.sort_order)).scalar() or 0
    cat = Category(key=key, name=name, emoji=emoji, sort_order=max_order + 1)
    db.session.add(cat)
    db.session.commit()
    flash(f"카테고리 '{emoji} {name}'이(가) 추가되었습니다.")
    return redirect(url_for("admin_categories"))

@app.route("/admin/categories/<int:cat_id>/toggle", methods=["POST"])
def admin_category_toggle(cat_id):
    if not is_admin():
        return jsonify({"error": "unauthorized"}), 401
    cat = Category.query.get_or_404(cat_id)
    cat.is_active = not cat.is_active
    db.session.commit()
    return jsonify({"ok": True, "is_active": cat.is_active})

@app.route("/admin/categories/<int:cat_id>/update", methods=["POST"])
def admin_category_update(cat_id):
    if not is_admin():
        return jsonify({"ok": False, "error": "권한 없음"}), 403
    cat = Category.query.get_or_404(cat_id)
    if cat.is_system:
        return jsonify({"ok": False, "error": "시스템 카테고리는 수정할 수 없습니다."})
    data = request.get_json()
    new_name = data.get("name", "").strip()
    if not new_name:
        return jsonify({"ok": False, "error": "이름을 입력하세요."})
    cat.name = new_name
    cat.key = new_name.lower().replace(" ", "_").replace("/", "_")
    db.session.commit()
    return jsonify({"ok": True})

@app.route("/admin/categories/<int:cat_id>/delete", methods=["POST"])
def admin_category_delete(cat_id):
    if not is_admin():
        return redirect(url_for("admin_login"))
    cat = Category.query.get_or_404(cat_id)
    if cat.is_system:
        flash("시스템 카테고리는 삭제할 수 없습니다.")
        return redirect(url_for("admin_categories"))
    db.session.delete(cat)
    db.session.commit()
    flash(f"카테고리가 삭제되었습니다.")
    return redirect(url_for("admin_categories"))


# ----------------------------
# Admin Store
# ----------------------------
@app.route("/admin/store")
def admin_store():
    if not is_admin():
        return redirect(url_for("admin_login"))
    return render_template("admin_store.html", products=StoreProduct.query.order_by(StoreProduct.id.desc()).all())

@app.route("/admin/store/new", methods=["GET", "POST"])
def admin_store_new():
    if not is_admin():
        return redirect(url_for("admin_login"))
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        if not title:
            flash("상품명을 입력하세요.", "error")
            return redirect(url_for("admin_store_new"))
        db.session.add(StoreProduct(
            title=title,
            category=(request.form.get("category") or "ebook").strip(),
            topic=(request.form.get("topic") or "shortform").strip(),
            price=int(request.form.get("price") or 0),
            description=(request.form.get("description") or "").strip(),
            image=(request.form.get("image") or "").strip(),
            file_url=(request.form.get("file_url") or "").strip(),
            badge=(request.form.get("badge") or "").strip(),
            is_active=request.form.get("is_active") in ("on", "1")
        ))
        db.session.commit()
        return redirect(url_for("admin_store"))
    return render_template("admin_store_new.html")

@app.route("/admin/store/<int:product_id>/edit", methods=["GET", "POST"])
def admin_store_edit(product_id):
    if not is_admin():
        return redirect(url_for("admin_login"))
    p = StoreProduct.query.get_or_404(product_id)
    if request.method == "POST":
        p.title = (request.form.get("title") or "").strip()
        p.category = (request.form.get("category") or "ebook").strip()
        p.topic = (request.form.get("topic") or "shortform").strip()
        p.price = int(request.form.get("price") or 0)
        p.description = (request.form.get("description") or "").strip()
        p.image = (request.form.get("image") or "").strip()
        p.file_url = (request.form.get("file_url") or "").strip()
        p.badge = (request.form.get("badge") or "").strip()
        p.is_active = request.form.get("is_active") in ("on", "1")
        db.session.commit()
        return redirect(url_for("admin_store"))
    return render_template("admin_store_edit.html", product=p)

@app.route("/admin/store/<int:product_id>/delete", methods=["POST"])
def admin_store_delete(product_id):
    if not is_admin():
        return redirect(url_for("admin_login"))
    db.session.delete(StoreProduct.query.get_or_404(product_id))
    db.session.commit()
    return redirect(url_for("admin_store"))


# ----------------------------
# Admin Subscriptions
# ----------------------------
@app.route("/admin/subscriptions")
def admin_subscriptions():
    if not is_admin():
        return redirect(url_for("admin_login"))
    return render_template("admin_subscriptions.html", subscriptions=Subscription.query.order_by(Subscription.id.desc()).all())

@app.route("/admin/subscriptions/add", methods=["GET", "POST"])
def admin_subscription_add():
    if not is_admin():
        return redirect(url_for("admin_login"))
    if request.method == "POST":
        user_email = (request.form.get("user_email") or "").strip().lower()
        plan_type = (request.form.get("plan_type") or "").strip()
        if not user_email or not plan_type:
            flash("이메일과 구독 타입을 입력하세요.", "error")
            return redirect(url_for("admin_subscription_add"))
        user = User.query.filter_by(email=user_email).first()
        if not user:
            flash("해당 이메일의 유저가 없습니다.", "error")
            return redirect(url_for("admin_subscription_add"))
        expires_at = None if plan_type == "profitguard_lifetime" else datetime.utcnow() + timedelta(days=int(request.form.get("days") or 30))
        db.session.add(Subscription(
            user_id=user.id, plan_type=plan_type, status="active",
            price=int(request.form.get("price") or 0), expires_at=expires_at
        ))
        db.session.commit()
        flash(f"{user_email}님에게 {plan_type} 구독이 추가되었습니다.", "success")
        return redirect(url_for("admin_subscriptions"))
    return render_template("admin_subscription_add.html")

@app.route("/admin/subscriptions/<int:sub_id>/cancel", methods=["POST"])
def admin_subscription_cancel(sub_id):
    if not is_admin():
        return redirect(url_for("admin_login"))
    sub = Subscription.query.get_or_404(sub_id)
    sub.status = "cancelled"
    db.session.commit()
    return redirect(url_for("admin_subscriptions"))


# ----------------------------
# APIs
# ----------------------------
@app.route("/api/upload_file", methods=["POST"])
def api_upload_file():
    if not is_admin():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "error": "no_file"}), 400
    saved = save_upload(f)
    if not saved:
        return jsonify({"ok": False, "error": "invalid_file"}), 400
    return jsonify({"ok": True, "filename": saved, "url": saved})

@app.route("/api/upload_profile_photo", methods=["POST"])
def api_upload_profile_photo():
    if not session.get("user_id"):
        return jsonify({"ok": False, "error": "로그인이 필요합니다."}), 401
    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "error": "파일이 없습니다."}), 400
    saved = save_upload(f)
    if not saved:
        return jsonify({"ok": False, "error": "업로드 실패"}), 400
    user = User.query.get(session["user_id"])
    if user:
        user.profile_photo = saved
        db.session.commit()
    return jsonify({"ok": True, "url": saved})

@app.route("/api/upload_video", methods=["POST"])
def api_upload_video():
    """[FIX #2,8] 전역 S3 클라이언트 + 환경변수 사용"""
    if not is_admin() and not session.get("is_seller"):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "error": "no_file"}), 400
    
    allowed_ext = {'mp4', 'mov', 'avi', 'webm', 'mkv'}
    ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
    if ext not in allowed_ext:
        return jsonify({"ok": False, "error": "invalid_format"}), 400
    
    import subprocess
    
    file_id = str(uuid.uuid4())
    temp_input = f"/tmp/{file_id}_input.{ext}"
    temp_output = f"/tmp/{file_id}_output.mp4"
    temp_thumb = f"/tmp/{file_id}_thumb.webp"
    
    f.save(temp_input)
    
    try:
        subprocess.run([
            '/usr/bin/ffmpeg', '-i', temp_input,
            '-vf', 'scale=-2:720',
            '-t', '60',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '28',
            '-c:a', 'aac', '-b:a', '128k',
            '-movflags', '+faststart',
            '-y', temp_output
        ], check=True, capture_output=True)
        
        subprocess.run([
            '/usr/bin/ffmpeg', '-i', temp_input,
            '-vf', 'scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2',
            '-frames:v', '1',
            '-y', temp_thumb
        ], check=True, capture_output=True)
        
        s3 = get_s3_client()
        
        with open(temp_output, 'rb') as vf:
            s3.upload_fileobj(vf, R2_BUCKET, f"{file_id}.mp4",
                ExtraArgs={'ContentType': 'video/mp4'})

        with open(temp_thumb, 'rb') as tf:
            s3.upload_fileobj(tf, R2_BUCKET, f"{file_id}_thumb.webp",
                ExtraArgs={'ContentType': 'image/webp'})

        video_url = f"/r2/{file_id}.mp4"
        thumb_url = f"/r2/{file_id}_thumb.webp"
        
        return jsonify({
            "ok": True, 
            "video_url": video_url,
            "thumb_url": thumb_url
        })
        
    except subprocess.CalledProcessError as e:
        return jsonify({"ok": False, "error": "compress_failed"}), 500
    finally:
        for tmp in [temp_input, temp_output, temp_thumb]:
            if os.path.exists(tmp):
                os.remove(tmp)

@app.route("/api/upload_public", methods=["POST"])
def api_upload_public():
    if not session.get("user_id"):
        return jsonify({"ok": False, "error": "login_required"}), 401
    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "error": "no_file"}), 400
    saved = save_upload(f)
    if not saved:
        return jsonify({"ok": False, "error": "invalid_file"}), 400
    return jsonify({"ok": True, "filename": saved, "url": saved})

@app.route("/api/save_post", methods=["POST"])
def api_save_post():
    if not is_admin():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    title = (request.form.get("title") or "").strip()
    if not title:
        return jsonify({"ok": False, "error": "title_required"}), 400
    
    last_post = Post.query.order_by(Post.id.desc()).first()
    next_num = (last_post.id + 1) if last_post else 1
    title = f"{next_num}. {title}"
    uploaded_by_id = session.get("user_id")
    images = parse_json_list_field("images_json")
    if not images:
        return jsonify({"ok": False, "error": "images_required"}), 400
    p = Post(
        title=title,
        category=(request.form.get("category") or "all").strip(),
        coupang_link=(request.form.get("coupang_link") or "").strip(),
        images_json=json.dumps(images, ensure_ascii=False),
        tags_json=json.dumps(parse_json_list_field("tags_json"), ensure_ascii=False),
        links_json=json.dumps(parse_json_list_field("links_json"), ensure_ascii=False),
        is_free=request.form.get("is_free") == "1",
        preview_video=(request.form.get("preview_video") or "").strip(),
        uploaded_by=session.get("user_id")
    )
    db.session.add(p)
    db.session.commit()
    return jsonify({"ok": True, "id": p.id, "redirect": url_for("admin_posts")})


# ----------------------------
# Dev (테스트용)
# ----------------------------
@app.route("/dev/sub/on")
def dev_sub_on():
    session["subscriber"] = True
    return redirect(url_for("index"))

@app.route("/dev/sub/off")
def dev_sub_off():
    session["subscriber"] = False
    return redirect(url_for("index"))


# ----------------------------
# 판매자 시스템
# ----------------------------
@app.route("/seller/apply", methods=["GET", "POST"])
def seller_apply():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    
    user = User.query.get(session["user_id"])
    if not user:
        return redirect(url_for("login"))
    
    if user.is_seller:
        flash("이미 판매자로 활동 중입니다.")
        return redirect(url_for("seller_dashboard"))
    if user.seller_status == "pending":
        flash("판매자 신청이 검토 중입니다.")
        return redirect(url_for("my_page"))
    
    if request.method == "POST":
        company = request.form.get("company", "").strip()
        category = request.form.get("category", "").strip()
        intro = request.form.get("intro", "").strip()
        
        if not company or not category:
            flash("업체명과 카테고리는 필수입니다.")
            return redirect(url_for("seller_apply"))
        
        user.seller_status = "pending"
        user.seller_company = company
        user.seller_category = category
        user.seller_intro = intro
        user.seller_applied_at = datetime.utcnow()
        db.session.commit()
        
        flash("판매자 신청이 완료되었습니다. 승인까지 1~2일 정도 소요됩니다.")
        return redirect(url_for("my_page"))
    
    return render_template("seller_apply.html", user=user)

@app.route("/seller/dashboard")
def seller_dashboard():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    user = User.query.get(session["user_id"])
    if not user or not user.is_seller:
        flash("판매자 전용 페이지입니다.")
        return redirect(url_for("my_page"))
    
    posts = Post.query.filter_by(seller_id=user.id).order_by(Post.created_at.desc()).all()
    return render_template("seller_dashboard.html", user=user, posts=posts)

@app.route("/seller/upload", methods=["GET", "POST"])
def seller_upload():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    user = User.query.get(session["user_id"])
    if not user or not user.is_seller:
        flash("판매자 전용 페이지입니다.")
        return redirect(url_for("my_page"))
    
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        coupang_link = request.form.get("coupang_link", "").strip()
        preview_video = request.form.get("preview_video", "").strip()
        images_json = request.form.get("images_json", "[]")
        links_json = request.form.get("links_json", "[]")
        
        try:
            images = json.loads(images_json)
            links = json.loads(links_json)
        except:
            images = []
            links = []
        
        if not title:
            return jsonify({"ok": False, "error": "제목은 필수입니다."})
        if not preview_video:
            return jsonify({"ok": False, "error": "영상을 업로드해주세요."})
        if not coupang_link:
            return jsonify({"ok": False, "error": "쿠팡 링크는 필수입니다."})
        
        images_str = json.dumps(images)
        links_str = json.dumps(links)
        
        video_url = links[0] if len(links) > 0 else ""
        video_url2 = links[1] if len(links) > 1 else ""
        video_url3 = links[2] if len(links) > 2 else ""
        
        post = Post(
            title=title,
            uploaded_by=uploaded_by_id,
            category="seller",
            images_json=images_str,
            links_json=links_str,
            video_url=video_url,
            video_url2=video_url2,
            video_url3=video_url3,
            coupang_link=coupang_link,
            is_free=False,
            seller_id=user.id,
            status="pending",
            preview_video=preview_video
        )
        db.session.add(post)
        db.session.commit()
        
        return jsonify({"ok": True, "message": "영상이 등록되었습니다."})
    
    return render_template("seller_upload.html", user=user)

@app.route("/revenue-proof/apply/<int:post_id>", methods=["POST"])
def revenue_proof_apply(post_id):
    if not session.get("user_id"):
        return jsonify({"ok": False, "error": "로그인이 필요합니다."}), 401
    
    if not session.get("subscriber") and not session.get("admin"):
        return jsonify({"ok": False, "error": "구독자만 신청할 수 있습니다."}), 403
    
    post = CommunityPost.query.get_or_404(post_id)
    user_id = session.get("user_id")
    
    if post.author_email != session.get("user_email"):
        return jsonify({"ok": False, "error": "본인 글만 신청 가능합니다."}), 403
    
    if post.reward_requested:
        return jsonify({"ok": False, "error": "이미 신청하셨습니다."}), 400
    
    existing = RevenueRewardHistory.query.filter_by(user_id=user_id, post_id=post_id).first()
    if existing:
        return jsonify({"ok": False, "error": "이미 신청한 기록이 있습니다."}), 400
    
    now = datetime.utcnow()
    first_day = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly_count = RevenueRewardHistory.query.filter(
        RevenueRewardHistory.user_id == user_id,
        RevenueRewardHistory.created_at >= first_day
    ).count()
    
    if monthly_count >= 3:
        return jsonify({"ok": False, "error": "월 3회까지만 신청 가능합니다."}), 400
    
    post.reward_requested = True
    db.session.add(RevenueRewardHistory(user_id=user_id, post_id=post_id))
    db.session.commit()
    
    return jsonify({"ok": True})

    
# 관리자 - 수익 인증 목록
@app.route("/admin/revenue-proofs")
def admin_revenue_proofs():
    if not is_admin():
        return redirect(url_for("admin_login"))
    
    proofs = RevenueRewardHistory.query.order_by(RevenueRewardHistory.created_at.desc()).all()
    for p in proofs:
        p.user = User.query.get(p.user_id)
        p.post = CommunityPost.query.get(p.post_id)
    return render_template("admin_revenue_proofs.html", proofs=proofs)

# 관리자 - 판매자 신청 목록



@app.route("/admin/revenue-proofs/<int:proof_id>/approve", methods=["POST"])

def admin_revenue_approve(proof_id):

    if not is_admin():

        return redirect(url_for("admin_login"))

    proof = RevenueRewardHistory.query.get_or_404(proof_id)

    proof.status = "approved"

    user = User.query.get(proof.user_id)

    if user:

        active_sub = Subscription.query.filter_by(user_id=user.id, status="active").first()

        if active_sub and active_sub.expires_at:

            active_sub.expires_at = active_sub.expires_at + timedelta(days=7)

        elif active_sub:

            active_sub.expires_at = datetime.utcnow() + timedelta(days=7)

    post = CommunityPost.query.get(proof.post_id)

    if post:

        post.reward_requested = True

    if user:

        expires_str = ""

        active_sub2 = Subscription.query.filter_by(user_id=user.id, status="active").first()

        if active_sub2 and active_sub2.expires_at:

            expires_str = active_sub2.expires_at.strftime("%Y-%m-%d")

        db.session.add(Notification(

            user_id=user.id,

            type="reward_approved",

            title="수익인증 리워드 승인",

            message=f"수익인증이 승인되어 구독이 7일 연장되었습니다! (다음 결제일: {expires_str})",

            link="/my/rewards"

        ))

    db.session.commit()

    return redirect(url_for("admin_revenue_proofs"))



@app.route("/admin/revenue-proofs/<int:proof_id>/reject", methods=["POST"])

def admin_revenue_reject(proof_id):

    if not is_admin():

        return redirect(url_for("admin_login"))

    proof = RevenueRewardHistory.query.get_or_404(proof_id)

    proof.status = "rejected"

    db.session.add(Notification(

        user_id=proof.user_id,

        type="revenue_rejected",

        title="수익인증 리워드 거절",

        message="수익인증이 거절되었습니다. 조건을 확인 후 다시 신청해주세요."

    ))

    post = CommunityPost.query.get(proof.post_id)

    if post:

        post.reward_approved = False

    db.session.commit()

    return redirect(url_for("admin_revenue_proofs"))


@app.route("/admin/sellers")
def admin_sellers():
    if not is_admin():
        return redirect(url_for("admin_login"))
    
    status_filter = request.args.get("status", "pending")
    if status_filter == "all":
        users = User.query.filter(User.seller_status.isnot(None)).order_by(User.seller_applied_at.desc()).all()
    else:
        users = User.query.filter_by(seller_status=status_filter).order_by(User.seller_applied_at.desc()).all()
    
    return render_template("admin_seller.html", users=users, status_filter=status_filter)

@app.route("/admin/sellers/<int:user_id>/approve", methods=["POST"])
def admin_seller_approve(user_id):
    if not is_admin():
        return jsonify({"ok": False}), 401
    
    user = User.query.get(user_id)
    if not user:
        return jsonify({"ok": False, "error": "user not found"}), 404
    
    user.seller_status = "approved"
    user.is_seller = True
    user.seller_approved_at = datetime.utcnow()
    
    db.session.add(Notification(
        user_id=user.id,
        type="seller_approved",
        title="판매자 승인 완료",
        message="판매자 신청이 승인되었습니다! 이제 상품을 등록할 수 있어요.",
        link="/seller/dashboard"
    ))
    db.session.commit()
    
    return jsonify({"ok": True})

@app.route("/admin/sellers/<int:user_id>/reject", methods=["POST"])
def admin_seller_reject(user_id):
    if not is_admin():
        return jsonify({"ok": False}), 401
    
    user = User.query.get(user_id)
    if not user:
        return jsonify({"ok": False, "error": "user not found"}), 404
    
    user.seller_status = "rejected"
    user.is_seller = False
    
    db.session.add(Notification(
        user_id=user.id,
        type="seller_rejected",
        title="판매자 신청 거절",
        message="판매자 신청이 거절되었습니다. 조건 확인 후 다시 신청해주세요.",
        link="/seller/apply"
    ))
    
    db.session.commit()
    
    return jsonify({"ok": True})



@app.route("/admin/sellers/<int:user_id>/revoke", methods=["POST"])

def admin_seller_revoke(user_id):

    if not is_admin():

        return jsonify({"ok": False}), 401

    user = User.query.get(user_id)

    if not user:

        return jsonify({"ok": False, "error": "user not found"}), 404

    user.seller_status = None

    user.is_seller = False

    db.session.add(Notification(

        user_id=user.id,

        type="seller_revoked",

        title="판매자 자격 해제",

        message="판매자 자격이 해제되었습니다. 문의사항은 오픈채팅으로 연락해주세요.",

        link="/seller/apply"

    ))

    db.session.commit()

    return jsonify({"ok": True})

# 관리자 - 판매자 게시물 승인
@app.route("/admin/seller-posts")
def admin_seller_posts():
    if not is_admin():
        return redirect(url_for("admin_login"))
    
    status_filter = request.args.get("status", "pending")
    if status_filter == "all":
        posts = Post.query.filter(Post.seller_id.isnot(None)).order_by(Post.created_at.desc()).all()
    else:
        posts = Post.query.filter(Post.seller_id.isnot(None), Post.status == status_filter).order_by(Post.created_at.desc()).all()
    
    return render_template("admin_seller_posts.html", posts=posts, status_filter=status_filter)

@app.route("/admin/seller-posts/<int:post_id>/approve", methods=["POST"])
def admin_seller_post_approve(post_id):
    if not is_admin():
        return jsonify({"ok": False}), 401
    
    post = Post.query.get(post_id)
    if not post:
        return jsonify({"ok": False, "error": "post not found"}), 404
    
    post.status = "approved"
    db.session.commit()
    
    return jsonify({"ok": True})

@app.route("/admin/seller-posts/<int:post_id>/reject", methods=["POST"])
def admin_seller_post_reject(post_id):
    if not is_admin():
        return jsonify({"ok": False}), 401
    
    post = Post.query.get(post_id)
    if not post:
        return jsonify({"ok": False, "error": "post not found"}), 404
    
    post.status = "rejected"
    db.session.commit()
    
    return jsonify({"ok": True})


# ----------------------------
# DB Init & Run
# ----------------------------
def init_default_categories():
    """기본 카테고리 초기화"""
    default_cats = [
        {"key": "all", "name": "전체보기", "emoji": "", "sort_order": 0, "is_system": True},
        {"key": "bookmark", "name": "찜한 영상", "emoji": "❤️", "sort_order": 1, "is_system": True},
        {"key": "recent", "name": "최근 본", "emoji": "🕐", "sort_order": 2, "is_system": True},
        {"key": "seller", "name": "판매자 직촬", "emoji": "📸", "sort_order": 3, "is_system": True},
        {"key": "beauty", "name": "Beauty", "emoji": "💄", "sort_order": 10, "is_system": False},
        {"key": "living", "name": "Living", "emoji": "🏠", "sort_order": 11, "is_system": False},
        {"key": "food", "name": "Food", "emoji": "🥗", "sort_order": 12, "is_system": False},
        {"key": "tech", "name": "Tech", "emoji": "💻", "sort_order": 13, "is_system": False},
    ]
    for cat in default_cats:
        existing = Category.query.filter_by(key=cat["key"]).first()
        if not existing:
            db.session.add(Category(**cat))
    db.session.commit()


with app.app_context():
    try:
        db.create_all()
        print("=== DB TABLES CREATED SUCCESSFULLY ===")
    except Exception as e:
        print(f"=== DB ERROR: {e} ===")
    init_default_categories()

# 에러 핸들러
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('500.html'), 500


# ============ 공구/협찬 ============
@app.route("/groupbuy")
def groupbuy_list():
    now = datetime.now()
    items = GroupBuy.query.filter(GroupBuy.status != "ended").order_by(GroupBuy.created_at.desc()).all()
    return render_template("groupbuy.html", items=items, now=now)

@app.route("/groupbuy/<int:item_id>")
def groupbuy_detail(item_id):
    item = GroupBuy.query.get_or_404(item_id)
    user_applied = False
    if session.get("user_id"):
        user_applied = GroupBuyApplication.query.filter_by(
            groupbuy_id=item_id, user_id=session["user_id"]
        ).first() is not None
    return render_template("groupbuy_detail.html", item=item, user_applied=user_applied)

@app.route("/groupbuy/<int:item_id>/apply", methods=["POST"])
def groupbuy_apply(item_id):
    if not session.get("user_id"):
        flash("로그인이 필요합니다.")
        return redirect(url_for("login"))
    
    item = GroupBuy.query.get_or_404(item_id)
    
    if item.is_ended() or item.status == "closed":
        flash("마감된 공구/협찬입니다.")
        return redirect(url_for("groupbuy_detail", item_id=item_id))
    
    if item.is_full():
        flash("신청 인원이 마감되었습니다.")
        return redirect(url_for("groupbuy_detail", item_id=item_id))
    
    if item.subscribers_only and not session.get("is_subscriber"):
        flash("구독자만 신청 가능합니다.")
        return redirect(url_for("groupbuy_detail", item_id=item_id))
    
    existing = GroupBuyApplication.query.filter_by(
        groupbuy_id=item_id, user_id=session["user_id"]
    ).first()
    if existing:
        flash("이미 신청하셨습니다.")
        return redirect(url_for("groupbuy_detail", item_id=item_id))
    
    application = GroupBuyApplication(
        groupbuy_id=item_id,
        user_id=session["user_id"],
        name=request.form.get("name", ""),
        phone=request.form.get("phone", ""),
        sns_url=request.form.get("sns_url", ""),
        message=request.form.get("message", "")
    )
    db.session.add(application)
    db.session.commit()
    
    flash("신청이 완료되었습니다!")
    return redirect(url_for("groupbuy_detail", item_id=item_id))


# ============ 트렌드 센터 ============
@app.route("/trend")
@cache.cached(timeout=600)
def trend_center():
    return render_template("trend.html")

@app.route("/api/youtube/trending")
@cache.cached(timeout=300, query_string=True)
def api_youtube_trending():
    import urllib.request
    import urllib.parse
    
    page_token = request.args.get("pageToken", "")
    url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics&chart=mostPopular&regionCode=KR&maxResults=50&key={YOUTUBE_API_KEY}"
    if page_token:
        url += f"&pageToken={page_token}"
    
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            
        videos = []
        for item in data.get("items", []):
            videos.append({
                "id": item["id"],
                "title": item["snippet"]["title"],
                "channel": item["snippet"]["channelTitle"],
                "thumbnail": item["snippet"]["thumbnails"]["high"]["url"],
                "views": int(item["statistics"].get("viewCount", 0)),
                "likes": int(item["statistics"].get("likeCount", 0)),
                "published": item["snippet"]["publishedAt"][:10]
            })
        return jsonify({
            "ok": True, 
            "videos": videos,
            "nextPageToken": data.get("nextPageToken", "")
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/youtube/search")
@cache.cached(timeout=300, query_string=True)
def api_youtube_search():
    import urllib.request
    import urllib.parse
    
    query = request.args.get("q", "")
    page_token = request.args.get("pageToken", "")
    if not query:
        return jsonify({"ok": False, "error": "검색어를 입력하세요"})
    
    encoded_query = urllib.parse.quote(query)
    url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&q={encoded_query}&type=video&order=viewCount&regionCode=KR&maxResults=50&key={YOUTUBE_API_KEY}"
    if page_token:
        url += f"&pageToken={page_token}"
    
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
        
        next_page_token = data.get("nextPageToken", "")
        video_ids = [item["id"]["videoId"] for item in data.get("items", [])]
        
        if video_ids:
            stats_url = f"https://www.googleapis.com/youtube/v3/videos?part=statistics&id={','.join(video_ids)}&key={YOUTUBE_API_KEY}"
            req2 = urllib.request.Request(stats_url)
            with urllib.request.urlopen(req2) as response2:
                stats_data = json.loads(response2.read().decode())
            
            stats_map = {}
            for item in stats_data.get("items", []):
                stats_map[item["id"]] = item["statistics"]
        else:
            stats_map = {}
        
        videos = []
        for item in data.get("items", []):
            vid = item["id"]["videoId"]
            stats = stats_map.get(vid, {})
            videos.append({
                "id": vid,
                "title": item["snippet"]["title"],
                "channel": item["snippet"]["channelTitle"],
                "thumbnail": item["snippet"]["thumbnails"]["high"]["url"],
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "published": item["snippet"]["publishedAt"][:10]
            })
        return jsonify({
            "ok": True, 
            "videos": videos,
            "nextPageToken": next_page_token
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/youtube/category/<category_id>")
@cache.cached(timeout=300, query_string=True)
def api_youtube_category(category_id):
    import urllib.request
    
    page_token = request.args.get("pageToken", "")
    url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics&chart=mostPopular&regionCode=KR&videoCategoryId={category_id}&maxResults=50&key={YOUTUBE_API_KEY}"
    if page_token:
        url += f"&pageToken={page_token}"
    
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            
        videos = []
        for item in data.get("items", []):
            videos.append({
                "id": item["id"],
                "title": item["snippet"]["title"],
                "channel": item["snippet"]["channelTitle"],
                "thumbnail": item["snippet"]["thumbnails"]["high"]["url"],
                "views": int(item["statistics"].get("viewCount", 0)),
                "likes": int(item["statistics"].get("likeCount", 0)),
                "published": item["snippet"]["publishedAt"][:10]
            })
        return jsonify({
            "ok": True, 
            "videos": videos,
            "nextPageToken": data.get("nextPageToken", "")
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/youtube/video/<video_id>")
@cache.cached(timeout=300)
def api_youtube_video_detail(video_id):
    import urllib.request
    
    url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics&id={video_id}&key={YOUTUBE_API_KEY}"
    
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
        
        if data.get("items"):
            item = data["items"][0]
            tags = item.get("snippet", {}).get("tags", [])
            comments = int(item.get("statistics", {}).get("commentCount", 0))
            return jsonify({
                "ok": True,
                "tags": tags,
                "comments": comments
            })
        return jsonify({"ok": False, "error": "not found"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/gallery")
@cache.cached(timeout=60, query_string=True)
def api_gallery():
    page = request.args.get("page", 1, type=int)
    per_page = 30
    category = request.args.get("category", "").strip()
    
    query = Post.query.filter(
        (Post.status == "approved") | (Post.status == None) | (Post.status == "")
    )
    
    if category and category not in ["all", ""]:
        query = query.filter_by(category=category)
    
    query = query.order_by(Post.is_free.desc(), Post.id.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    
    posts = []
    for p in pagination.items:
        posts.append(p.to_dict())
    
    return jsonify({
        "posts": posts,
        "has_next": pagination.has_next,
        "page": page,
        "total": pagination.total
    })


# ----------------------------
# 내가 쓴 글
# ----------------------------
@app.route("/my/posts")
def my_posts():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    
    posts = CommunityPost.query.filter_by(
        author_email=session.get("user_email")
    ).order_by(CommunityPost.created_at.desc()).all()
    
    applications = DealApplication.query.filter_by(
        user_id=session.get("user_id")
    ).order_by(DealApplication.created_at.desc()).all()
    
    return render_template("my_posts.html", posts=posts, applications=applications)

@app.route("/my/link-requests")
def my_link_requests():
    return redirect("/link-requests/new")


# ----------------------------
# 리워드
# ----------------------------
@app.route("/my/rewards")
def my_rewards():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    
    user = User.query.get(session["user_id"])
    
    if not user.referral_code:
        import random
        import string
        user.referral_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        db.session.commit()
    
    invited_users = User.query.filter_by(referred_by=user.id).all()
    
    revenue_posts = CommunityPost.query.filter_by(
        author_email=session.get("user_email"),
        category="revenue"
    ).order_by(CommunityPost.created_at.desc()).all()
    

    

    # 각 게시글에 리워드 상태 매핑

    for post in revenue_posts:

        reward = RevenueRewardHistory.query.filter_by(user_id=user.id, post_id=post.id).first()

        if reward:

            post.reward_approved = (reward.status == "approved")

            post.reward_requested = True

        else:

            post.reward_approved = False

            post.reward_requested = False

    

    # 이번 달 승인/대기 리워드 수

    first_day = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    monthly_approved = RevenueRewardHistory.query.filter(

        RevenueRewardHistory.user_id == user.id,

        RevenueRewardHistory.created_at >= first_day,

        RevenueRewardHistory.status == "approved"

    ).count()

    monthly_pending = RevenueRewardHistory.query.filter(

        RevenueRewardHistory.user_id == user.id,

        RevenueRewardHistory.created_at >= first_day,

        RevenueRewardHistory.status == "pending"

    ).count()




    rejected_post_ids = [r.post_id for r in RevenueRewardHistory.query.filter_by(user_id=user.id, status="rejected").all()]

    return render_template("my_rewards.html", 
        user=user,
        invited_users=invited_users,
        revenue_posts=revenue_posts,
        monthly_approved=monthly_approved,
        monthly_pending=monthly_pending,
        rejected_post_ids=rejected_post_ids
    )

@app.route("/my/nickname", methods=["GET", "POST"])
def my_nickname():
    if "user_id" not in session:
        return redirect(url_for("login", next="/my/nickname"))
    
    user = User.query.get(session["user_id"])
    if not user:
        return redirect(url_for("login"))
    
    if request.method == "POST":
        new_nickname = (request.form.get("nickname") or "").strip()
        if new_nickname:
            user.nickname = new_nickname
            db.session.commit()
            return redirect(url_for("my_page"))
        return render_template("my_nickname.html", user=user, error="닉네임을 입력해주세요")
    
    return render_template("my_nickname.html", user=user)


# ----------------------------
# 결제 내역
# ----------------------------
@app.route("/my/payments")
def my_payments():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    
    payments = []
    
    return render_template("my_payments.html", payments=payments)


# ----------------------------
# 판매자: 내 공구/협찬 관리
# ----------------------------
@app.route("/my/deals")
def my_deals():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    
    my_posts = CommunityPost.query.filter_by(
        author_email=session.get("user_email"),
        category="deal"
    ).order_by(CommunityPost.created_at.desc()).all()
    
    return render_template("my_deals.html", posts=my_posts, now=datetime.utcnow())


@app.route("/my/deals/<int:post_id>")
def my_deal_detail(post_id):
    if not session.get("user_id"):
        return redirect(url_for("login"))
    
    post = CommunityPost.query.get_or_404(post_id)
    
    if post.author_email != session.get("user_email") and not session.get("admin"):
        flash("접근 권한이 없습니다.", "error")
        return redirect(url_for("my_deals"))
    
    applications = DealApplication.query.filter_by(post_id=post_id).order_by(DealApplication.created_at.desc()).all()
    
    return render_template("my_deal_detail.html", post=post, applications=applications)


@app.route("/my/deals/<int:post_id>/approve/<int:app_id>", methods=["POST"])
def deal_approve(post_id, app_id):
    if not session.get("user_id"):
        return jsonify({"ok": False, "error": "login_required"})
    
    post = CommunityPost.query.get_or_404(post_id)
    if post.author_email != session.get("user_email") and not session.get("admin"):
        return jsonify({"ok": False, "error": "no_permission"})
    
    application = DealApplication.query.get_or_404(app_id)
    application.status = "approved"
    
    noti = Notification(
        user_id=application.user_id,
        type="deal_approved",
        title="공구/협찬 승인",
        message=f"'{post.title}' 신청이 승인되었습니다!",
        link=f"/community/{post_id}"
    )
    db.session.add(noti)
    db.session.commit()
    
    return jsonify({"ok": True})


@app.route("/my/deals/<int:post_id>/reject/<int:app_id>", methods=["POST"])
def deal_reject(post_id, app_id):
    if not session.get("user_id"):
        return jsonify({"ok": False, "error": "login_required"})
    
    post = CommunityPost.query.get_or_404(post_id)
    if post.author_email != session.get("user_email") and not session.get("admin"):
        return jsonify({"ok": False, "error": "no_permission"})
    
    application = DealApplication.query.get_or_404(app_id)
    application.status = "rejected"
    
    noti = Notification(
        user_id=application.user_id,
        type="deal_rejected",
        title="공구/협찬 미승인",
        message=f"'{post.title}' 신청이 승인되지 않았습니다.",
        link=f"/community/{post_id}"
    )
    db.session.add(noti)
    db.session.commit()
    
    return jsonify({"ok": True})


@app.route("/my/deals/<int:post_id>/close", methods=["POST"])
def deal_close(post_id):
    if not session.get("user_id"):
        return jsonify({"ok": False, "error": "login_required"})
    
    post = CommunityPost.query.get_or_404(post_id)
    if post.author_email != session.get("user_email") and not session.get("admin"):
        return jsonify({"ok": False, "error": "no_permission"})
    
    # [FIX #4] deal_closed → is_deal_available 사용
    post.is_deal_available = False
    db.session.commit()
    
    return jsonify({"ok": True})


# ----------------------------
# 고객지원
# ----------------------------
@app.route("/support")
@cache.cached(timeout=3600)
def support():
    return render_template("support.html")


# ----------------------------
# 알림
# ----------------------------
@app.route("/notifications")
def notifications():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    
    notis = Notification.query.filter(Notification.user_id==session["user_id"], ~Notification.type.like("quest_%")).order_by(Notification.created_at.desc()).limit(50).all()
    
    return render_template("notifications.html", notifications=notis, timedelta=timedelta)

@app.route("/api/notifications/mark-read", methods=["POST"])
def api_notifications_mark_read():
    if not session.get("user_id"):
        return jsonify({"ok": False}), 401
    
    Notification.query.filter_by(user_id=session["user_id"], is_read=False).update({"is_read": True})
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/notifications/count")
def api_notifications_count():
    from flask import make_response
    if not session.get("user_id"):
        resp = make_response(jsonify({"count": 0, "user_id": None}))
    else:
        user_id = session["user_id"]
        count = Notification.query.filter(Notification.user_id==user_id, Notification.is_read==False, ~Notification.type.like("quest_%")).count()
        resp = make_response(jsonify({"count": count, "user_id": user_id}))
    
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp

    
@app.route("/api/notifications/<int:noti_id>/delete", methods=["POST"])
def api_notification_delete(noti_id):
    if not session.get("user_id"):
        return jsonify({"ok": False, "error": "login_required"})
    
    noti = Notification.query.get_or_404(noti_id)
    if noti.user_id != session["user_id"]:
        return jsonify({"ok": False, "error": "no_permission"})
    
    db.session.delete(noti)
    db.session.commit()
    return jsonify({"ok": True})

@app.route("/api/notifications/delete-all", methods=["POST"])
def api_notifications_delete_all():
    if not session.get("user_id"):
        return jsonify({"ok": False, "error": "login_required"})
    
    Notification.query.filter_by(user_id=session["user_id"]).delete()
    db.session.commit()
    return jsonify({"ok": True})






def _apply_profitguard_event(user):

    import secrets as _secrets, string as _string

    if EventTrialApply.query.filter_by(email=user.email).first():

        return

    if EventTrialApply.query.count() >= 100:

        return

    db.session.add(EventTrialApply(email=user.email, phone=user.phone or "", user_id=user.id))

    temp_pw = None

    if not user.pg_password_set:

        temp_pw = "".join(_secrets.choice(_string.ascii_letters + _string.digits) for _ in range(10))

        user.pw_hash = generate_password_hash(temp_pw)

        user.pg_password_set = True

    existing_sub = Subscription.query.filter_by(user_id=user.id, plan_type="profitguard_pro", status="active").first()

    if not existing_sub:

        db.session.add(Subscription(user_id=user.id, plan_type="profitguard_pro", status="active", price=0, expires_at=datetime.utcnow() + timedelta(days=14)))

    db.session.commit()

    if user.phone:

        try:

            pw_text = temp_pw if temp_pw else "(existing password)"

            msg = "\ud504\ub85c\ud54f\uac00\ub4dc 14\uc77c \ubb34\ub8cc\uccb4\ud5d8 \uc2e0\uccad\uc774 \uc644\ub8cc\ub418\uc5c8\uc2b5\ub2c8\ub2e4.\n\n\uc774\uba54\uc77c: " + user.email + "\n\uc784\uc2dc \ube44\ubc00\ubc88\ud638: " + pw_text + "\n\uccb4\ud5d8 \uae30\uac04: 14\uc77c\n\n\uc544\ub798 \ubc84\ud2bc\uc5d0\uc11c \ub85c\uadf8\uc778 \ud6c4\n\ud504\ub85c\ud54f\uac00\ub4dc\ub97c \ub2e4\uc6b4\ub85c\ub4dc\ud558\uc138\uc694."

            button = {"button": [{"name": "\ucc44\ub110 \ucd94\uac00", "type": "AC"}, {"name": "\ud504\ub85c\ud54f\uac00\ub4dc \ubc14\ub85c\uac00\uae30", "type": "WL", "url_mobile": "https://moneying.biz/profitguard", "url_pc": "https://moneying.biz/profitguard"}]}

            send_alimtalk(user.phone, "\ud504\ub85c\ud54f\uac00\ub4dc \ubb34\ub8cc\uccb4\ud5d8 \uc548\ub0b4", msg, "UF_4244", button)

        except Exception as e:

            print(f"[PG EVENT] alimtalk fail: {e}")



@app.route("/api/me")

def api_me():

    uid = session.get("user_id")

    if not uid:

        return jsonify({"ok": False})

    return jsonify({"ok": True, "user_id": uid, "email": session.get("user_email", "")})



@app.route("/api/profitguard-event/apply-loggedin", methods=["POST"])

def profitguard_event_apply_loggedin():

    uid = session.get("user_id")

    if not uid:

        return jsonify({"ok": False, "error": "login required"})

    user = User.query.get(uid)

    if not user:

        return jsonify({"ok": False, "error": "user not found"})

    try:

        _apply_profitguard_event(user)

        return jsonify({"ok": True, "count": EventTrialApply.query.count()})

    except Exception as e:

        return jsonify({"ok": False, "error": str(e)})



@app.route("/api/profitguard-event/count")

def profitguard_event_count():

    count = EventTrialApply.query.count()

    resp = jsonify({"ok": True, "count": count})

    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"

    return resp



@app.route("/api/profitguard-event/apply", methods=["POST"])

def profitguard_event_apply():

    import secrets as _secrets, string as _string

    data = request.get_json() or {}

    email = (data.get("email") or "").strip().lower()

    if not email or "@" not in email:

        return jsonify({"ok": False, "error": "이메일을 올바르게 입력해주세요."})

    if EventTrialApply.query.count() >= 100:

        return jsonify({"ok": False, "error": "무료체험이 마감되었습니다."})

    if EventTrialApply.query.filter_by(email=email).first():

        return jsonify({"ok": False, "error": "이미 신청하셨습니다."})

    db.session.add(EventTrialApply(email=email))

    user = User.query.filter_by(email=email).first()

    temp_pw = "".join(_secrets.choice(_string.ascii_letters + _string.digits) for _ in range(10))

    if not user:

        user = User(email=email, pw_hash=generate_password_hash(temp_pw))

        db.session.add(user)

        db.session.flush()

    else:

        temp_pw = None

    existing_sub = Subscription.query.filter_by(user_id=user.id, plan_type="profitguard_pro", status="active").first()

    if not existing_sub:

        db.session.add(Subscription(user_id=user.id, plan_type="profitguard_pro", status="active", price=0, expires_at=datetime.utcnow() + timedelta(days=14)))

    db.session.commit()

    dl = "https://moneying.biz/profitguard"

    if temp_pw:

        html = '<div style="font-family:sans-serif;max-width:500px;margin:0 auto;padding:20px;"><h2 style="color:#c4ff00;background:#0a0a0a;padding:20px;border-radius:12px;text-align:center;">PROFIT GUARD 무료체험</h2><p>프로핏가드 14일 무료체험 신청이 완료되었습니다.</p><div style="background:#f5f5f5;padding:16px;border-radius:8px;margin:16px 0;"><p><strong>이메일:</strong> ' + email + '</p><p><strong>임시 비밀번호:</strong> ' + temp_pw + '</p><p><strong>체험 기간:</strong> 14일</p></div><p>아래에서 프로핏가드를 다운로드하고 로그인하세요.</p><p style="text-align:center;margin:24px 0;"><a href="' + dl + '" style="background:#c4ff00;color:#000;padding:12px 32px;border-radius:8px;text-decoration:none;font-weight:bold;">프로핏가드 다운로드</a></p></div>'

    else:

        html = '<div style="font-family:sans-serif;max-width:500px;margin:0 auto;padding:20px;"><h2 style="color:#c4ff00;background:#0a0a0a;padding:20px;border-radius:12px;text-align:center;">PROFIT GUARD 무료체험</h2><p>프로핏가드 14일 무료체험 신청이 완료되었습니다.</p><div style="background:#f5f5f5;padding:16px;border-radius:8px;margin:16px 0;"><p><strong>이메일:</strong> ' + email + '</p><p><strong>비밀번호:</strong> 기존 MONEYING 비밀번호 사용</p><p><strong>체험 기간:</strong> 14일</p></div><p>아래에서 프로핏가드를 다운로드하고 로그인하세요.</p><p style="text-align:center;margin:24px 0;"><a href="' + dl + '" style="background:#c4ff00;color:#000;padding:12px 32px;border-radius:8px;text-decoration:none;font-weight:bold;">프로핏가드 다운로드</a></p></div>'

    try:

        send_email(email, "[MONEYING] 프로핏가드 14일 무료체험 안내", html)

    except Exception as e:

        print(f"[EVENT] 이메일 발송 실패: {e}")

    return jsonify({"ok": True, "count": EventTrialApply.query.count()})



@app.route("/profitguard-event")
def profitguard_event():
    return render_template("profitguard_event.html")

# --- onboarding quest ---
@app.route("/api/onboarding/status")
def api_onboarding_status():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"ok": False})
    user = User.query.get(uid)
    if not user or user.onboarding_done:
        return jsonify({"ok": True, "done": True, "quests": []})
    from sqlalchemy import text
    quests = []
    done_ids = set()
    try:
        with db.engine.connect() as conn:
            r = conn.execute(text("SELECT COUNT(*) FROM notification WHERE user_id=:uid AND type='quest_gallery'"), {"uid": uid})
            if r.scalar() > 0: done_ids.add("gallery")
            r = conn.execute(text("SELECT COUNT(*) FROM notification WHERE user_id=:uid AND type='quest_profitguard'"), {"uid": uid})
            if r.scalar() > 0: done_ids.add("profitguard")
            r = conn.execute(text("SELECT COUNT(*) FROM notification WHERE user_id=:uid AND type='quest_chrome'"), {"uid": uid})
            if r.scalar() > 0: done_ids.add("chrome")
            r = conn.execute(text("SELECT COUNT(*) FROM link_request WHERE requester_email=(SELECT email FROM user WHERE id=:uid)"), {"uid": uid})
            if r.scalar() > 0: done_ids.add("linkreq")
    except:
        pass
    quests.append({"id": "gallery", "title": "gallery", "done": "gallery" in done_ids, "link": "/gallery"})
    quests.append({"id": "profitguard", "title": "profitguard", "done": "profitguard" in done_ids, "link": "/profitguard"})
    quests.append({"id": "chrome", "title": "chrome", "done": "chrome" in done_ids, "link": "/store/chrome-extension"})
    quests.append({"id": "linkreq", "title": "linkreq", "done": "linkreq" in done_ids, "link": "/link-requests/new"})
    done_count = len(done_ids)
    all_done = done_count == len(quests)
    if all_done and not user.onboarding_done:
        user.onboarding_done = True
        db.session.commit()
    return jsonify({"ok": True, "done": all_done, "quests": quests, "done_count": done_count, "total": len(quests)})

@app.route("/api/onboarding/visit", methods=["POST"])
def api_onboarding_visit():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"ok": False})
    data = request.get_json() or {}
    quest_id = data.get("quest_id", "")
    valid = {"gallery": "quest_gallery", "profitguard": "quest_profitguard", "chrome": "quest_chrome"}
    ntype = valid.get(quest_id)
    if not ntype:
        return jsonify({"ok": False})
    from sqlalchemy import text
    with db.engine.connect() as conn:
        r = conn.execute(text("SELECT COUNT(*) FROM notification WHERE user_id=:uid AND type=:t"), {"uid": uid, "t": ntype})
        if r.scalar() == 0:
            db.session.add(Notification(user_id=uid, type=ntype, title="", message=""))
            db.session.commit()
    return jsonify({"ok": True})

@app.route("/api/onboarding/complete", methods=["POST"])
def api_onboarding_complete():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"ok": False})
    user = User.query.get(uid)
    if user:
        user.onboarding_done = True
        db.session.commit()
    return jsonify({"ok": True})

