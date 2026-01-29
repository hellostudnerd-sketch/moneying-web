import os
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
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET", "moneying-perfect-final-safe")
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL or ("sqlite:///" + os.path.join(BASE_DIR, "database.db"))
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# 이메일 설정
MAIL_SERVER = os.getenv("MAIL_SERVER", "smtp.gmail.com")
MAIL_PORT = int(os.getenv("MAIL_PORT", 587))
MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
MAIL_FROM = os.getenv("MAIL_FROM", "noreply@moneying.co.kr")

db = SQLAlchemy(app)
@app.after_request
def add_header(response):
    if 'text/html' in response.content_type or 'application/json' in response.content_type:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response
    
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "1234")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@moneying.com")

# 링크요청 월 제한
LINK_REQUEST_LIMIT_FREE = 3
LINK_REQUEST_LIMIT_SUBSCRIBER = 10


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


class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)  # beauty, living 등
    name = db.Column(db.String(100), nullable=False)  # 💄 Beauty 등
    emoji = db.Column(db.String(10), nullable=True, default="")
    sort_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    is_system = db.Column(db.Boolean, default=False)  # 시스템 카테고리 (삭제 불가)
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
    
    # 영상 URL (video_url1~3)
    video_url = db.Column(db.Text, nullable=True, default="")
    video_url2 = db.Column(db.Text, nullable=True, default="")
    video_url3 = db.Column(db.Text, nullable=True, default="")
    
    # 판매자 관련
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    status = db.Column(db.String(20), default="approved")  # pending, approved, rejected

    def to_dict(self):
        def safe_list(s):
            try:
                v = json.loads(s) if s else []
                return v if isinstance(v, list) else []
            except Exception:
                return []
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
    user = db.relationship('User', backref='subscriptions')

    def is_active(self):
        if self.status != "active":
            return False
        if self.expires_at is None:
            return True
        return self.expires_at > datetime.utcnow()


class GroupBuy(db.Model):
    """공구/협찬 모델"""
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    image = db.Column(db.String(500), nullable=True)
    category = db.Column(db.String(50), default="groupbuy")  # groupbuy, sponsorship
    
    # 신청 조건
    max_participants = db.Column(db.Integer, default=0)  # 0이면 무제한
    subscribers_only = db.Column(db.Boolean, default=True)
    
    # 기간
    start_date = db.Column(db.DateTime, default=datetime.utcnow)
    end_date = db.Column(db.DateTime, nullable=True)
    
    # 상태
    status = db.Column(db.String(20), default="open")  # open, closed, ended
    
    # 추가 정보
    brand = db.Column(db.String(100), nullable=True)
    benefit = db.Column(db.Text, nullable=True)  # 혜택 설명
    requirements = db.Column(db.Text, nullable=True)  # 신청 조건
    contact = db.Column(db.String(200), nullable=True)  # 연락처
    
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
    
    # 신청 정보
    name = db.Column(db.String(50), nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    sns_url = db.Column(db.String(500), nullable=True)  # SNS 채널 주소
    message = db.Column(db.Text, nullable=True)  # 신청 메시지
    
    status = db.Column(db.String(20), default="pending")  # pending, approved, rejected
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    groupbuy = db.relationship('GroupBuy', backref='applications')
    user = db.relationship('User', backref='groupbuy_applications')
    
    __table_args__ = (db.UniqueConstraint('groupbuy_id', 'user_id'),)


class Report(db.Model):
    """신고 모델"""
    id = db.Column(db.Integer, primary_key=True)
    reporter_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # 신고 대상
    target_type = db.Column(db.String(20), nullable=False)  # post, comment, user
    target_id = db.Column(db.Integer, nullable=False)
    
    reason = db.Column(db.String(50), nullable=False)  # spam, abuse, inappropriate, etc
    description = db.Column(db.Text, nullable=True)
    
    status = db.Column(db.String(20), default="pending")  # pending, reviewed, resolved
    admin_note = db.Column(db.Text, nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    resolved_at = db.Column(db.DateTime, nullable=True)
    
    reporter = db.relationship('User', backref='reports_made')


class UserBlock(db.Model):
    """차단 모델"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    reason = db.Column(db.String(100), nullable=True)
    blocked_until = db.Column(db.DateTime, nullable=True)  # null이면 영구차단
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)  # 차단한 관리자
    
    user = db.relationship('User', foreign_keys=[user_id], backref='blocks')


class Notification(db.Model):
    """알림 모델"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    type = db.Column(db.String(50), nullable=True)  # deal_approved, deal_rejected, reward, comment 등
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=True)
    link = db.Column(db.String(500), nullable=True)
    
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref='notifications')


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
    return dict(
        get_nickname=get_nickname
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
    print("R2_BUCKET:", os.getenv('R2_BUCKET'))  # 디버깅용
    if not file_storage or not file_storage.filename:
        return ""
    filename = secure_filename(file_storage.filename)
    ext = os.path.splitext(filename)[1].lower()
    if ext and ext not in ALLOWED_EXT:
        return ""
    
    import boto3
    from io import BytesIO
    from PIL import Image
    
    # 이미지 압축
    img = Image.open(file_storage)
    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')
    img.thumbnail((1200, 1200), Image.LANCZOS)
    
    buffer = BytesIO()
    img.save(buffer, 'WEBP', quality=80)
    buffer.seek(0)
    
    # R2 업로드
    new_name = f"{uuid.uuid4().hex}.webp"
    
    s3 = boto3.client('s3',
        endpoint_url="https://b6f9c47a567f57911cab3c58f07cfc61.r2.cloudflarestorage.com",
        aws_access_key_id="bd378a5b4a8c51dece8aeeec96c846e5",
        aws_secret_access_key="f7001674ed1ee7f505a45f071891811db5e333c2a890f4f9f71a7f7be41c55f7"
    )
    
    s3.upload_fileobj(
        buffer,
        "moneying-uploads",
        new_name,
        ExtraArgs={'ContentType': 'image/webp'}
    )
    
    # R2 Public URL 반환
    return f"https://pub-a2e6030a78b240aea6b998d958ae5b83.r2.dev/{new_name}"

def parse_json_list_field(field_name: str):
    raw = (request.form.get(field_name) or "[]").strip()
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else []
    except Exception:
        return []


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
    """무료 체험 중인지 확인"""
    if not user_id:
        return False
    user = User.query.get(user_id)
    if not user or not user.free_trial_expires:
        return False
    return user.free_trial_expires > datetime.utcnow()

def can_use_free_trial(user_id):
    """무료 체험 사용 가능한지 확인"""
    if not user_id:
        return False
    user = User.query.get(user_id)
    if not user:
        return False
    # 이미 사용했거나 구독 중이면 불가
    if user.free_trial_used:
        return False
    if get_user_subscriptions(user_id):
        return False
    return True

def get_trial_expires_at(user_id):
    """체험 만료일 반환"""
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
    # 구독자 또는 체험자는 10회
    if has_active_subscription(user_id, "gallery") or \
       has_active_subscription(user_id, "allinone") or \
       is_trial_active(user_id):
        return LINK_REQUEST_LIMIT_SUBSCRIBER
    return LINK_REQUEST_LIMIT_FREE

def can_make_link_request(user_id, user_email):
    return get_monthly_link_request_count(user_email) < get_link_request_limit(user_id)


# ----------------------------
# 세션 업데이트 헬퍼
# ----------------------------
def update_session_status(user_id):
    """세션의 구독/체험 상태 업데이트"""
    if not user_id:
        return
    
    # 체험 상태
    if is_trial_active(user_id):
        session["is_trial"] = True
        session["subscriber"] = True
    else:
        session["is_trial"] = False
        # 실제 구독 확인
        if get_user_subscriptions(user_id):
            session["subscriber"] = True
        else:
            session["subscriber"] = False


# ----------------------------
# Public Routes
# ----------------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/store")
def store():
    products = StoreProduct.query.filter_by(is_active=True).order_by(StoreProduct.id.desc()).all()
    return render_template("store.html", products=products)

@app.route("/store/chrome-extension")
def store_chrome_extension():
    return render_template("store_chrome_extension.html")


@app.route("/store/<int:product_id>")
def store_detail(product_id):
    product = StoreProduct.query.get_or_404(product_id)
    if not product.is_active and not is_admin():
        abort(404)
    return render_template("store_detail.html", product=product)

@app.route("/pricing")
def pricing():
    can_use_trial = False
    if session.get("user_id"):
        user = db.session.get(User, session["user_id"])
        if user and not user.free_trial_used:
            can_use_trial = True
    return render_template("pricing.html", can_use_trial=can_use_trial)

@app.route("/gallery")
def gallery():
    # 승인된 게시물만 표시 (status가 approved이거나 없는 경우)
    # FREE(is_free=True) 게시물 먼저, 그 다음 최신순
    posts = Post.query.filter(
        (Post.status == "approved") | (Post.status == None) | (Post.status == "")
    ).order_by(Post.is_free.desc(), Post.id.desc()).all()
    
    # 세션 상태 업데이트
    user_id = session.get("user_id")
    update_session_status(user_id)
    
    # 판매자 상태 업데이트
    if user_id:
        try:
            user = User.query.get(user_id)
            session["is_seller"] = user.is_seller if user and hasattr(user, 'is_seller') else False
        except:
            session["is_seller"] = False
    
    return render_template("gallery.html", posts=[p.to_dict() for p in posts])

@app.route("/community")
def community_page():
    posts = CommunityPost.query.order_by(CommunityPost.id.desc()).limit(50).all()
    my_linkreq_count = 0
    if session.get("user_email"):
        my_linkreq_count = LinkRequest.query.filter_by(requester_email=session["user_email"]).count()
    return render_template("community.html", posts=posts, my_linkreq_count=my_linkreq_count)

@app.route("/community/<int:post_id>")
def community_detail(post_id):
    post = CommunityPost.query.get_or_404(post_id)
    comments = CommunityComment.query.filter_by(post_id=post_id).order_by(CommunityComment.created_at).all()
    like_count = CommunityLike.query.filter_by(post_id=post_id).count()
    user_liked = False
    if session.get("user_email"):
        user_liked = CommunityLike.query.filter_by(post_id=post_id, user_email=session.get("user_email")).first() is not None
    return render_template("community_detail.html", post=post, comments=comments, like_count=like_count, user_liked=user_liked)

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

@app.route("/community/<int:post_id>/like", methods=["POST"])
def community_like(post_id):
    if not session.get("user_id"):
        return redirect(url_for("login", next=f"/community/{post_id}"))
    user_email = session.get("user_email")
    existing = CommunityLike.query.filter_by(post_id=post_id, user_email=user_email).first()
    if existing:
        db.session.delete(existing)
    else:
        db.session.add(CommunityLike(post_id=post_id, user_email=user_email))
    db.session.commit()
    return redirect(url_for("community_detail", post_id=post_id))

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
    if session.get("admin"):
        return redirect(url_for("admin_posts"))

    user_email = session.get("user_email", "")
    user_id = session.get("user_id")
    
    # 세션 상태 업데이트
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
    
    # 체험 관련
    trial_expires_at = get_trial_expires_at(user_id)
    can_trial = can_use_free_trial(user_id)
    
    # 판매자 정보
    user = User.query.get(user_id)
    user_is_seller = user.is_seller if user else False
    user_seller_status = user.seller_status if user else None
    user_seller_company = user.seller_company if user else None
    user_seller_category = user.seller_category if user else None

    # 친구 초대 코드 생성 (없으면 생성)
    if not user.referral_code:
        import random
        import string
        user.referral_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        db.session.commit()
    
    # 초대한 친구 수
    invited_count = User.query.filter_by(referred_by=user.id).count()

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
        invited_count=invited_count
    )

@app.route("/profitguard")
def profitguard_page():
    return render_template("profitguard.html")

@app.route("/proof")
def proof_page():
    return render_template("proof.html")

@app.route("/subscribe-info")
def subscribe_info():
    return render_template("subscribe.html")

@app.route("/terms")
def terms():
    return render_template("terms.html")

@app.route("/privacy")
def privacy():
    return render_template("privacy.html")

@app.route("/refund")
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
    
    # 이미 체험 사용했는지 확인
    if user.free_trial_used:
        flash("이미 무료 체험을 사용하셨습니다.", "error")
        return redirect(url_for("pricing"))
    
    # 이미 구독자인지 확인
    if get_user_subscriptions(user.id):
        flash("이미 구독 중이십니다.", "error")
        return redirect(url_for("gallery"))
    
    if request.method == "POST":
        # 3일 무료 체험 시작
        user.free_trial_used = True
        user.free_trial_expires = datetime.utcnow() + timedelta(days=3)
        db.session.commit()
        
        session["is_trial"] = True
        session["subscriber"] = True
        
        flash("🎉 3일 무료 체험이 시작되었습니다!", "success")
        return redirect(url_for("gallery"))
    
    return render_template("free_trial.html")


@app.route("/community/write", methods=["GET", "POST"])
def community_write():
    if not session.get("user_id"):
        return redirect(url_for("login", next="/community/write"))
    
    # 판매자 여부 확인해서 세션에 저장
    user = User.query.get(session.get("user_id"))
    if user:
        session["is_seller"] = user.is_seller
    
    if request.method == "POST":
        category = (request.form.get("category") or "free").strip()
        title = (request.form.get("title") or "").strip()
        content = (request.form.get("content") or "").strip()
        images = parse_json_list_field("images_json")
        
        # 공구/협찬은 판매자만 가능
        if category == "deal" and not (session.get("is_seller") or session.get("admin")):
            flash("공구/협찬 글은 판매자만 작성할 수 있습니다.", "error")
            return redirect(url_for("community_write"))
        
        if not title or not content:
            flash("제목/내용을 입력해주세요.", "error")
            return redirect(url_for("community_write"))
        db.session.add(CommunityPost(
            category=category, title=title, content=content,
            author_email=session.get("user_email", "guest"),
            images_json=json.dumps(images, ensure_ascii=False)
        ))
        db.session.commit()
        return redirect(url_for("community_page"))
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
    
    # 세션 상태 업데이트
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
        flash("링크요청이 접수되었습니다!", "success")
        return redirect(url_for("link_requests"))

    return render_template("link_request_new.html", monthly_used=monthly_used, monthly_limit=monthly_limit)

@app.route("/link-requests")
def link_requests():
    user_id = session.get("user_id")
    user_email = session.get("user_email", "")

    # 세션 상태 업데이트
    if user_id:
        update_session_status(user_id)

    if session.get("admin"):
        items = LinkRequest.query.order_by(LinkRequest.id.desc()).all()
        monthly_used, monthly_limit = 0, 999
    elif user_id:
        items = LinkRequest.query.filter_by(requester_email=user_email).order_by(LinkRequest.id.desc()).all()
        monthly_used = get_monthly_link_request_count(user_email)
        monthly_limit = get_link_request_limit(user_id)
    else:
        # 비로그인 - 빈 목록
        items = []
        monthly_used, monthly_limit = 0, 3

    return render_template("link_requests.html", items=items, monthly_used=monthly_used, monthly_limit=monthly_limit)

@app.route("/link-requests/<int:request_id>", methods=["GET", "POST"])
def link_request_detail(request_id):
    it = LinkRequest.query.get_or_404(request_id)
    if not session.get("admin"):
        if not session.get("user_id"):
            return redirect(url_for("login", next=f"/link-requests/{request_id}"))
        if it.requester_email != session.get("user_email", ""):
            abort(403)
    if request.method == "POST":
        if not session.get("admin"):
            abort(403)
        it.coupang_url = (request.form.get("coupang_url") or "").strip()
        db.session.commit()
        return redirect(url_for("link_request_detail", request_id=request_id))
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
            return redirect(url_for("register"))
        if User.query.filter_by(email=email).first():
            flash("이미 가입된 이메일입니다.", "error")
            return redirect(url_for("register"))
        
        # 초대코드로 추천인 찾기
        referred_by_id = None
        if referral_code:
            referrer = User.query.filter_by(referral_code=referral_code).first()
            if referrer:
                referred_by_id = referrer.id
        
        u = User(email=email, pw_hash=generate_password_hash(password), referred_by=referred_by_id)
        db.session.add(u)
        db.session.commit()
        
        # 추천인에게 7일 연장
        if referred_by_id:
            referrer = User.query.get(referred_by_id)
            if referrer:
                # 추천인의 활성 구독 찾기
                active_sub = Subscription.query.filter(
                    Subscription.user_id == referrer.id,
                    Subscription.expires_at > datetime.utcnow()
                ).order_by(Subscription.expires_at.desc()).first()
                
                if active_sub:
                    # 기존 구독에 7일 추가
                    active_sub.expires_at = active_sub.expires_at + timedelta(days=7)
                    db.session.commit()
        
        session.clear()
        session["user_id"] = u.id
        session["user_email"] = u.email
        session["subscriber"] = False
        session["is_trial"] = False
        return redirect(url_for("index"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("admin"):
        return redirect(url_for("admin_home"))
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = (request.form.get("password") or "").strip()
        next_url = (request.form.get("next") or "").strip()
        
        # 관리자 이메일인 경우
        if email == ADMIN_EMAIL and password == ADMIN_PASSWORD:
            session.clear()
            session["admin"] = True
            return redirect(url_for("admin_home"))
        
        # 일반 유저 로그인
        u = User.query.filter_by(email=email).first()
        
        # 계정 잠금 확인
        if u and u.locked_until:
            if datetime.now() < u.locked_until:
                remaining = (u.locked_until - datetime.now()).seconds // 60 + 1
                flash(f"로그인 시도 5회 실패로 계정이 잠겼습니다. {remaining}분 후 다시 시도해주세요.", "error")
                return redirect(url_for("login"))
            else:
                # 잠금 해제
                u.locked_until = None
                u.login_fail_count = 0
                db.session.commit()
        
        # 비밀번호 확인
        if not u or not check_password_hash(u.pw_hash, password):
            # 실패 횟수 증가
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
        
        # 로그인 성공 - 실패 횟수 초기화
        u.login_fail_count = 0
        u.locked_until = None
        db.session.commit()
        
        session.clear()
        session["user_id"] = u.id
        session["user_email"] = u.email
        
        # 세션 상태 업데이트
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
        
        # 임시 비밀번호 생성
        import random
        import string
        temp_pw = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        user.pw_hash = generate_password_hash(temp_pw)
        db.session.commit()
        
        # 이메일 발송
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
            # 이메일 발송 실패 시 화면에 표시 (개발용)
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
    return redirect(url_for("index"))


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
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    
    from datetime import date
    today = date.today()
    
    # 오늘 통계
    try:
        today_users = User.query.filter(db.func.date(User.created_at) == today).count()
    except:
        today_users = 0
    
    try:
        today_links = LinkRequest.query.filter(db.func.date(LinkRequest.created_at) == today).count()
    except:
        today_links = 0
    
    # 처리 필요 (대기중)
    pending_links = LinkRequest.query.filter((LinkRequest.coupang_url == None) | (LinkRequest.coupang_url == "")).count()
    
    # 판매자 대기 (TODO: seller_status 컬럼 확인)
    pending_sellers = 0
    
    # 수익인증 대기 (TODO: reward_requested 컬럼 추가 후 활성화)
    pending_rewards = 0
    
    # 판매자 게시물 승인 대기
    pending_posts = Post.query.filter_by(status="pending").count()
    
    return render_template("admin_home.html",
        # 오늘 통계
        today=today,
        today_users=today_users,
        today_links=today_links,
        
        # 처리 필요
        pending_links=pending_links,
        pending_sellers=pending_sellers,
        pending_rewards=pending_rewards,
        pending_posts=pending_posts,
        
        # 전체 통계
        user_count=User.query.count(),
        subscriber_count=db.session.query(Subscription.user_id).filter_by(status="active").distinct().count(),
        gallery_count=Post.query.count(),
        store_count=StoreProduct.query.count(),
        
        # 최근 활동
        recent_link_requests=LinkRequest.query.order_by(desc(LinkRequest.id)).limit(5).all(),
        recent_posts=Post.query.order_by(desc(Post.id)).limit(5).all()
    )

@app.route("/admin/pending-posts")
def admin_pending_posts():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    
    posts = Post.query.filter_by(status="pending").order_by(desc(Post.id)).all()
    return render_template("admin_pending_posts.html", posts=posts)


@app.route("/admin/pending-posts/<int:post_id>/approve", methods=["POST"])
def admin_approve_post(post_id):
    if not session.get("admin"):
        return jsonify({"ok": False, "error": "권한 없음"})
    
    post = Post.query.get_or_404(post_id)
    post.status = "approved"
    db.session.commit()
    
    # 판매자에게 알림
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
    if not session.get("admin"):
        return jsonify({"ok": False, "error": "권한 없음"})
    
    post = Post.query.get_or_404(post_id)
    post.status = "rejected"
    db.session.commit()
    
    # 판매자에게 알림
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
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    from datetime import datetime
    
    users = User.query.order_by(desc(User.id)).all()
    
    # 각 유저의 구독/체험 상태 추가
    now = datetime.utcnow()
    subscriber_count = 0
    trial_count = 0
    
    for user in users:
        # 구독 상태 확인
        active_sub = Subscription.query.filter(
            Subscription.user_id == user.id,
            Subscription.status == "active",
            (Subscription.expires_at == None) | (Subscription.expires_at > now)
        ).first()
        user.subscriber = active_sub is not None
        
        # 체험 상태 확인
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
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    return render_template("admin_stats.html",
        gallery_count=Post.query.count(),
        link_count=LinkRequest.query.count(),
        user_count=User.query.count(),
        store_count=StoreProduct.query.count(),
        subscriber_count=db.session.query(Subscription.user_id).filter_by(status="active").distinct().count()
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
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    return render_template("admin_gallery.html", posts=Post.query.order_by(Post.id.desc()).all())

@app.route("/admin/gallery/bulk")
def admin_gallery_bulk():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    categories = Category.query.filter(
        Category.is_active == True,
        Category.key.notin_(['all', 'bookmark', 'recent'])
    ).order_by(Category.sort_order).all()
    return render_template("admin_gallery_bulk.html", categories=categories)

@app.route("/admin/gallery/bulk/sample")
def admin_gallery_bulk_sample():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    # UTF-8 BOM 추가 (엑셀 한글 깨짐 방지)
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
    if not session.get("admin"):
        return jsonify({"error": "unauthorized"}), 401
    
    import csv
    import io
    
    file = request.files.get("csv_file")
    if not file:
        return jsonify({"error": "파일이 없습니다"}), 400
    
    try:
        raw_data = file.read()
        content = None
        
        # 여러 인코딩 시도 (엑셀 CSV는 보통 CP949)
        for encoding in ["utf-8-sig", "utf-8", "cp949", "euc-kr"]:
            try:
                content = raw_data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        
        if content is None:
            return jsonify({"error": "파일 인코딩을 인식할 수 없습니다"}), 400
        
        # 탭으로 구분된 파일인지 확인 (엑셀 기본 저장)
        lines = content.strip().split('\n')
        delimiter = '\t' if '\t' in lines[0] else ','
        
        # 헤더 정리 (공백 제거)
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
                is_free=row.get("is_free", "0").strip() == "1"
            )
            db.session.add(post)
            count += 1
        
        db.session.commit()
        return jsonify({"ok": True, "count": count})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/admin/posts")
def admin_posts():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    return redirect("/admin/gallery")


@app.route("/admin/gallery/bulk-delete", methods=["POST"])
def admin_gallery_bulk_delete():
    if not session.get("admin"):
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
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    # 활성화된 카테고리 목록 (시스템 카테고리 제외: all, bookmark, recent)
    categories = Category.query.filter(
        Category.is_active == True,
        Category.key.notin_(['all', 'bookmark', 'recent'])
    ).order_by(Category.sort_order).all()
    return render_template("admin_upload.html", categories=categories)

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
    return render_template("admin_edit.html", post=p, post_data=p.to_dict(), next_url=next_url)

@app.route("/admin/posts/<int:post_id>/delete", methods=["POST"])
def admin_delete(post_id):
    if not is_admin():
        return redirect(url_for("admin_login"))
    db.session.delete(Post.query.get_or_404(post_id))
    db.session.commit()
    return redirect(url_for("admin_posts"))


# ----------------------------
# Admin Link Requests
# ----------------------------
@app.route("/admin/link-requests")
def admin_link_requests():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    return render_template("admin_link_requests.html", items=LinkRequest.query.order_by(LinkRequest.id.desc()).all())


# ----------------------------
# Admin Categories
# ----------------------------
@app.route("/admin/categories")
def admin_categories():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    categories = Category.query.order_by(Category.sort_order).all()
    return render_template("admin_categories.html", categories=categories)

@app.route("/admin/categories/add", methods=["POST"])
def admin_category_add():
    if not session.get("admin"):
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
    if not session.get("admin"):
        return jsonify({"error": "unauthorized"}), 401
    cat = Category.query.get_or_404(cat_id)
    cat.is_active = not cat.is_active
    db.session.commit()
    return jsonify({"ok": True, "is_active": cat.is_active})

@app.route("/admin/categories/<int:cat_id>/delete", methods=["POST"])
def admin_category_delete(cat_id):
    if not session.get("admin"):
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
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    return render_template("admin_store.html", products=StoreProduct.query.order_by(StoreProduct.id.desc()).all())

@app.route("/admin/store/new", methods=["GET", "POST"])
def admin_store_new():
    if not session.get("admin"):
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
    if not session.get("admin"):
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
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    db.session.delete(StoreProduct.query.get_or_404(product_id))
    db.session.commit()
    return redirect(url_for("admin_store"))


# ----------------------------
# Admin Subscriptions
# ----------------------------
@app.route("/admin/subscriptions")
def admin_subscriptions():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    return render_template("admin_subscriptions.html", subscriptions=Subscription.query.order_by(Subscription.id.desc()).all())

@app.route("/admin/subscriptions/add", methods=["GET", "POST"])
def admin_subscription_add():
    if not session.get("admin"):
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
    if not session.get("admin"):
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
    return jsonify({"ok": True, "filename": saved, "url": url_for("static", filename=f"uploads/{saved}")})

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
    return jsonify({"ok": True, "filename": saved, "url": url_for("static", filename=f"uploads/{saved}")})

@app.route("/api/save_post", methods=["POST"])
def api_save_post():
    if not is_admin():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    title = (request.form.get("title") or "").strip()
    if not title:
        return jsonify({"ok": False, "error": "title_required"}), 400
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
        is_free=request.form.get("is_free") == "1"
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
    
    # 이미 판매자이거나 신청 중인 경우
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
    
    # 판매자가 올린 게시물 조회
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
        images_json = request.form.get("images_json", "[]")
        links_json = request.form.get("links_json", "[]")
        
        import json
        try:
            images = json.loads(images_json)
            links = json.loads(links_json)
        except:
            images = []
            links = []
        
        # 필수값 검증
        if not title:
            return jsonify({"ok": False, "error": "제목은 필수입니다."})
        if not images or len(images) == 0:
            return jsonify({"ok": False, "error": "하이라이트 캡처 이미지를 1장 이상 업로드해주세요."})
        if not coupang_link:
            return jsonify({"ok": False, "error": "쿠팡 링크는 필수입니다."})
        
        # images를 JSON 문자열로 저장
        images_str = json.dumps(images)
        
        # links 분리
        video_url = links[0] if len(links) > 0 else ""
        video_url2 = links[1] if len(links) > 1 else ""
        video_url3 = links[2] if len(links) > 2 else ""
        
        post = Post(
            title=title,
            category="seller",  # 판매자 직촬로 고정
            images_json=images_str,
            video_url=video_url,
            video_url2=video_url2,
            video_url3=video_url3,
            coupang_link=coupang_link,
            is_free=False,
            seller_id=user.id,
            status="pending"  # 관리자 승인 대기
        )
        db.session.add(post)
        db.session.commit()
        
        return jsonify({"ok": True, "message": "영상이 등록되었습니다."})
    
    return render_template("seller_upload.html", user=user)

# 관리자 - 수익 인증 목록
@app.route("/admin/revenue-proofs")
def admin_revenue_proofs():
    if not is_admin():
        return redirect(url_for("admin_login"))
    
    # 수익인증 카테고리 글 조회
    proofs = CommunityPost.query.filter_by(category="revenue").order_by(CommunityPost.created_at.desc()).all()
    return render_template("admin_revenue_proofs.html", proofs=proofs)

# 관리자 - 판매자 신청 목록
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
    db.create_all()
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
    
    # 마감 체크
    if item.is_ended() or item.status == "closed":
        flash("마감된 공구/협찬입니다.")
        return redirect(url_for("groupbuy_detail", item_id=item_id))
    
    # 인원 체크
    if item.is_full():
        flash("신청 인원이 마감되었습니다.")
        return redirect(url_for("groupbuy_detail", item_id=item_id))
    
    # 구독자 전용 체크
    if item.subscribers_only and not session.get("is_subscriber"):
        flash("구독자만 신청 가능합니다.")
        return redirect(url_for("groupbuy_detail", item_id=item_id))
    
    # 중복 신청 체크
    existing = GroupBuyApplication.query.filter_by(
        groupbuy_id=item_id, user_id=session["user_id"]
    ).first()
    if existing:
        flash("이미 신청하셨습니다.")
        return redirect(url_for("groupbuy_detail", item_id=item_id))
    
    # 신청 저장
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
YOUTUBE_API_KEY = "AIzaSyDRnCHasdEJ3ARExoAsfqmnZiwp1oPrjNQ"

@app.route("/trend")
def trend_center():
    return render_template("trend.html")

@app.route("/api/youtube/trending")
def api_youtube_trending():
    """한국 인기 급상승 영상"""
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
def api_youtube_search():
    """키워드 검색"""
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
def api_youtube_category(category_id):
    """카테고리별 인기 영상"""
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


@app.route("/api/gallery")
def api_gallery():
    page = request.args.get("page", 1, type=int)
    per_page = 30
    category = request.args.get("category", "").strip()
    
    # 승인된 게시물만 표시
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
    
    return render_template("my_posts.html", posts=posts)


# ----------------------------
# 리워드
# ----------------------------
@app.route("/my/rewards")
def my_rewards():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    
    user = User.query.get(session["user_id"])
    
    # 친구 초대 코드 (없으면 생성)
    if not user.referral_code:
        import random
        import string
        user.referral_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        db.session.commit()
    
    invited_users = User.query.filter_by(referred_by=user.id).all()
    
    # 내 수익인증 글
    revenue_posts = CommunityPost.query.filter_by(
        author_email=session.get("user_email"),
        category="revenue"
    ).order_by(CommunityPost.created_at.desc()).all()
    
    return render_template("my_rewards.html", 
        user=user,
        invited_users=invited_users,
        revenue_posts=revenue_posts
    )


# ----------------------------
# 결제 내역
# ----------------------------
@app.route("/my/payments")
def my_payments():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    
    # 나중에 결제 연동하면 여기서 가져옴
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
    
    # 본인 글인지 확인
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
    
    # 신청자에게 알림
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
    
    # 신청자에게 알림
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
    
    post.deal_closed = True
    db.session.commit()
    
    return jsonify({"ok": True})


# ----------------------------
# 고객지원
# ----------------------------
@app.route("/support")
def support():
    return render_template("support.html")


# ----------------------------
# 알림
# ----------------------------
@app.route("/notifications")
def notifications():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    
    notis = Notification.query.filter_by(user_id=session["user_id"]).order_by(Notification.created_at.desc()).limit(50).all()
    
    # 읽음 처리
    Notification.query.filter_by(user_id=session["user_id"], is_read=False).update({"is_read": True})
    db.session.commit()
    
    return render_template("notifications.html", notifications=notis)


@app.route("/api/notifications/count")
def api_notifications_count():
    if not session.get("user_id"):
        return jsonify({"count": 0})
    
    count = Notification.query.filter_by(user_id=session["user_id"], is_read=False).count()
    return jsonify({"count": count})


if __name__ == "__main__":
    app.run(debug=True)
