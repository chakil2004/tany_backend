from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
from functools import wraps
import os
import math
from dotenv import load_dotenv
from supabase import create_client, Client
import json
from flask_cors import cross_origin
from flask import make_response
import xml.etree.ElementTree as ET
from datetime import datetime
import tempfile
from shapely.geometry import shape
from shapely.errors import ShapelyError
from shapely.ops import transform
import geojson
from werkzeug.exceptions import RequestEntityTooLarge

load_dotenv()

app = Flask(__name__)
#CORS(app)  # Enable CORS for frontend requests
CORS(app, origins=["http://127.0.0.1:5500", "http://127.0.0.1:5501", "http://localhost:5500", "http://localhost:5501", "https://tany-main-v1-b0gt2r9v5-michel-raherimanantsoas-projects.vercel.app", "https://tany-main-v1.vercel.app", "https://tany-main-v1-o52dpd7ax-michel-raherimanantsoas-projects.vercel.app", "https://tany-main-v1-jp8zipbm9-michel-raherimanantsoas-projects.vercel.app","https://michelheriman.github.io/tany_main_v1/", "https://michelheriman.github.io", "https://tany-main-v1-git-main-michel-raherimanantsoas-projects.vercel.app", "https://tanylandrustv2.vercel.app"], supports_credentials=True)
# OR for more specific control:
#CORS(app, resources={r"/api/*": {"origins": "http://127.0.0.1:5500"}})

def _deprecated_getcoords_response():
    response = make_response(jsonify({
        'success': False,
        'message': 'This endpoint is deprecated'
    }), 410)
    response.headers['Deprecation'] = 'true'
    return response

###fafana rehefa uploaded###
@app.before_request
def handle_preflight():
    if request.method == "OPTIONS":
        if request.path == '/api/getcoords':
            return _deprecated_getcoords_response()

        res = make_response()
        res.headers['Access-Control-Allow-Origin'] = request.headers.get('Origin', '*')
        res.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        res.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        res.headers['Access-Control-Max-Age'] = '3600'
        res.status_code = 200
        return res
############################

basedir = os.path.abspath(os.path.dirname(__file__))
# Join it with the .env filename
load_dotenv(os.path.join(basedir, '.env'))

# Configuration from .env
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

# Initialize Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Decorator to require authentication
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # --- FIX STARTS HERE ---
        if request.method == 'OPTIONS':
            return f(None, *args, **kwargs) # Pass None as current_user for OPTIONS
        # --- FIX ENDS HERE ---
        auth_header = request.headers.get('Authorization')

        if not auth_header:
            return jsonify({'success': False, 'message': 'Authorization header is missing'}), 401

        # Ensure it follows the "Bearer <token>" format
        parts = auth_header.split()
        if parts[0].lower() != 'bearer' or len(parts) != 2:
            return jsonify({'success': False, 'message': 'Authorization header must be "Bearer <token>"'}), 401

        token = parts[1]

        try:
            supabase_auth_response = supabase.auth.get_user(token)

            if not supabase_auth_response.user:
                return jsonify({'success': False, 'message': 'Invalid token or user not found'}), 401

            # Get user profile from profiles table
            profile_query = supabase.table('profiles').select('*').eq('uuid', supabase_auth_response.user.id).execute()

            if not profile_query.data:
                return jsonify({'success': False, 'message': 'User profile not found'}), 401

            current_user = profile_query.data[0]

        except Exception as e:
            app.logger.warning("Supabase token validation failed (%s)", type(e).__name__)
            return jsonify({'success': False, 'message': 'Invalid or expired token'}), 401

        return f(current_user, *args, **kwargs)

    return decorated

@app.route('/check-username', methods=['POST'])
def check_username():
    data = request.json
    username = data.get('username', '').strip()

    if not username:
        return jsonify({"available": False, "message": "Username is empty"}), 400

    try:
        response = supabase.table("profiles") \
            .select("username", count="exact") \
            .eq("username", username) \
            .execute()

        is_available = response.count == 0

        return jsonify({
            "available": is_available,
            "username": username
        }), 200

    except Exception:
        app.logger.exception("Username availability lookup failed")
        return jsonify({"error": "Internal server error"}), 500

@app.route('/api/parcels_w', methods=['POST', 'OPTIONS'])
@token_required
def draw_parcels(current_user):
    if request.method == 'OPTIONS':
        return jsonify({'success': True}), 200

    owner_email = current_user.get('email') if isinstance(current_user, dict) else None
    if not isinstance(owner_email, str) or not owner_email.strip():
        return jsonify({'success': False, 'message': 'Authenticated profile has no email'}), 403

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': 'A JSON object is required'}), 400

    # 1. Validation & Extraction
    fields = ["owner", "price_m2", "price", "currency", "location_name",
              "description", "sell_category","status_category", "longitude",
              "latitude", "parcel_name", "geojson", "area_ha"]

    parcel_d = {}
    for field in fields:
        val = data.get(field)
        #if val is None or val == "": # Better check than 'if not val' for numbers
        if val is None or (isinstance(val, str) and val.strip() == ""):
            return jsonify({'success': False, 'message': f'{field} is required'}), 400
        parcel_d[field] = val
    parcel_d['owner_uuid'] = owner_email

    try:
        # 2. Critical Fix: Convert geojson string to a Python Dict (JSONB in Postgres)
        if isinstance(parcel_d['geojson'], str):
            parcel_d['geojson'] = json.loads(parcel_d['geojson'])

        # 3. Type Casting (Ensure numbers aren't strings)
        parcel_d['price'] = float(parcel_d['price'])
        parcel_d['price_m2'] = float(parcel_d['price_m2'])
        parcel_d['area_ha'] = float(parcel_d['area_ha'])

        # 4. Insert to Supabase
        # Changed table name to 'parcels' (Assuming 'profiles' was a typo)
        response = supabase.table('main_parcels').insert(parcel_d).execute()

        return jsonify({
            "success": True,
            "message": "Parcel registered successfully",
            "data": response.data
        }), 201

    except Exception:
        app.logger.exception("Parcel creation failed")
        return jsonify({
            "success": False,
            "message": "Failed to create parcel"
        }), 500

@app.route('/api/messages_w', methods=['POST', 'OPTIONS'])
@token_required
def write_messages(current_user):
    if request.method == 'OPTIONS':
        return jsonify({'success': True}), 200

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': 'A JSON object is required'}), 400

    sender_id = current_user.get('email') if isinstance(current_user, dict) else None
    if not isinstance(sender_id, str) or not sender_id.strip():
        return jsonify({'success': False, 'message': 'Authenticated profile has no email'}), 403

    fields = ["receiver_id", "content", "uuid_post", "objet"]
    parcel_d = {}
    for field in fields:
        val = data.get(field)
        if not isinstance(val, str) or not val.strip():
            return jsonify({'success': False, 'message': f'{field} must be a non-empty string'}), 400
        parcel_d[field] = val
    parcel_d['sender_id'] = sender_id

    try:

        response = supabase.table('messages').insert(parcel_d).execute()

        return jsonify({
            "success": True,
            "message": "message sent",
            "data": response.data
        }), 201

    except Exception:
        app.logger.exception("Message insertion failed")
        return jsonify({
            "success": False,
            "message": "Failed to send message"
        }), 500

@app.route('/api/current_user', methods=['GET'])  # Changed from POST to GET (more appropriate for getting data)
@token_required
def get_current_user_email(current_user):
    """Get current user email from JWT token"""
    try:
        # Since you're using @token_required, current_user is already passed
        # This contains the user data from your database
        email = current_user.get('email')

        if not email:
            # Use Supabase Auth again for a verified identity fallback.
            auth_header = request.headers.get('Authorization')
            if auth_header:
                parts = auth_header.split()
                if parts[0].lower() == 'bearer' and len(parts) == 2:
                    token = parts[1]
                    auth_user = supabase.auth.get_user(token).user
                    user_metadata = getattr(auth_user, 'user_metadata', None)
                    user_metadata = user_metadata if isinstance(user_metadata, dict) else {}
                    email = (
                        getattr(auth_user, 'email', None) or
                        user_metadata.get('user_email') or
                        user_metadata.get('email') or
                        getattr(auth_user, 'id', None)
                    )

        if email:
            return jsonify({
                'success': True,
                'message': 'User email retrieved successfully',
                'data': {
                    'email': email,
                    'user_id': current_user.get('uuid') or current_user.get('id'),
                    'name': current_user.get('name'),
                    'username': current_user.get('username')
                }
            }), 200
        else:
            return jsonify({
                'success': False,
                'message': 'Email not found in user data or token',
                'data': None
            }), 404

    except Exception:
        app.logger.exception("Current user lookup failed")
        return jsonify({
            'success': False,
            'message': 'Failed to retrieve user information',
            'data': None
        }), 500

@app.route('/api/register', methods=['POST'])
def register():
    """Register a new user using Supabase Auth"""
    data = request.get_json()

    # account remains accepted from legacy clients but is not trusted.
    required_fields = ['email', 'password', 'name', 'username', 'mobile']
    for field in required_fields:
        if not data or not data.get(field):
            return jsonify({
                'success': False,
                'message': f'{field.capitalize()} is required'
            }), 400

    email = data['email']
    password = data['password']
    name = data['name']
    username = data['username']
    mobile = data['mobile']
    #address = data['address']
    #id_card = data['idCard']
    account_type = 'free'

    try:
        # Check if username already exists in profiles table
        existing_username = supabase.table('profiles').select('username').eq('username', username).execute()

        if existing_username.data and len(existing_username.data) > 0:
            return jsonify({
                'success': False,
                'message': 'Username already exists'
            }), 409

        # Check if email already exists
        existing_email = supabase.table('profiles').select('email').eq('email', email).execute()

        if existing_email.data and len(existing_email.data) > 0:
            return jsonify({
                'success': False,
                'message': 'Email already exists'
            }), 409

        # Sign up user with Supabase Auth
        auth_response = supabase.auth.sign_up({
            'email': email,
            'password': password,
            'options': {
                'data': {
                    'name': name,
                    'username': username
                }
            }
        })

        if auth_response.user:
            # Store additional user profile data in profiles table
            profile_data = {
                'uuid': auth_response.user.id,
                'name': name,
                'username': username,
                'mobile': mobile,
                #'address': address,
                #'id_card': id_card,
                'account': account_type,
                'email': email
            }

            supabase.table('profiles').insert(profile_data).execute()

            return jsonify({
                'success': True,
                'message': 'User registered successfully',
                'user': {
                    'id': auth_response.user.id,
                    'email': auth_response.user.email,
                    'name': name,
                    'username': username
                }
            }), 201
        else:
            return jsonify({
                'success': False,
                'message': 'Registration failed'
            }), 400

    except Exception as e:
        error_message = str(e)
        if 'already registered' in error_message.lower() or 'user already exists' in error_message.lower():
            return jsonify({
                'success': False,
                'message': 'Email already exists'
            }), 409
        app.logger.exception("Registration failed")
        return jsonify({
            'success': False,
            'message': 'Registration failed'
        }), 500

@app.route('/api/login', methods=['POST'])
def login():
    """Authenticate user using Supabase Auth"""
    data = request.get_json()

    # Validate input
    if not data or not data.get('email') or not data.get('password'):
        return jsonify({
            'success': False,
            'message': 'Email and password are required'
        }), 400

    email = data['email']
    password = data['password']

    try:
        # Sign in with Supabase Auth
        auth_response = supabase.auth.sign_in_with_password({
            'email': email,
            'password': password
        })

        if auth_response.user and auth_response.session:
            return jsonify({
                'success': True,
                'message': 'Login successful',
                'token': auth_response.session.access_token,
                'refresh_token': auth_response.session.refresh_token,
                'user': {
                    'id': auth_response.user.id,
                    'email': auth_response.user.email,
                    'name': auth_response.user.user_metadata.get('name', '')
                }
            }), 200
        else:
            return jsonify({
                'success': False,
                'message': 'Invalid credentials'
            }), 401

    except Exception as e:
        error_message = str(e)
        if 'Invalid login credentials' in error_message or 'invalid' in error_message.lower():
            return jsonify({
                'success': False,
                'message': 'Invalid email or password'
            }), 401
        app.logger.exception("Login failed")
        return jsonify({
            'success': False,
            'message': 'Login failed'
        }), 500

@app.route('/api/protected', methods=['GET'])
@token_required
def protected_route(current_user):
    """Example of a protected route that requires authentication"""
    return jsonify({
        'success': True,
        'message': 'This is protected data',
        'user': {
            'id': current_user['uuid'],
            'email': current_user['email'],
            'name': current_user.get('name', '')
        }
    }), 200

@app.route('/api/verify', methods=['GET'])
@token_required
def verify_token(current_user):
    """Verify if token is valid"""
    return jsonify({
        'success': True,
        'message': 'Token is valid',
        'user': {
            'id': current_user['uuid'],
            'email': current_user['email'],
            'name': current_user.get('name', '')
        }
    }), 200

@app.route('/api/load_message', methods=['GET', 'POST'])
@token_required
def get_messages(current_user):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': 'A JSON object is required'}), 400

    received_value = data.get('data')
    if not isinstance(received_value, str) or not received_value.strip():
        return jsonify({'success': False, 'message': 'A conversation subject is required'}), 400

    current_email = current_user.get('email') if isinstance(current_user, dict) else None
    if not isinstance(current_email, str) or not current_email.strip():
        return jsonify({'success': False, 'message': 'Authenticated profile has no email'}), 403

    try:
        result = supabase.table('messages').select('*').eq("objet", received_value).execute()
        messages = result.data or []
        if not messages:
            return jsonify({'success': False, 'message': 'Conversation not found'}), 404

        participant_messages = [
            message for message in messages
            if message.get('sender_id') == current_email or message.get('receiver_id') == current_email
        ]
        if not participant_messages:
            return jsonify({'success': False, 'message': 'You are not a participant in this conversation'}), 403

        return jsonify({
            'success': True,
            'data': participant_messages
        }), 200
    except Exception:
        app.logger.exception("Message fetch failed")
        return jsonify({
            'success': False,
            'message': 'Failed to fetch messages'
        }), 500

@app.route('/api/load_object', methods=['GET', 'POST'])
@token_required
def get_objects(current_user):
    if request.method == 'POST':
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({'success': False, 'message': 'A JSON object is required'}), 400
        if 'data' in data and (not isinstance(data['data'], str) or not data['data'].strip()):
            return jsonify({'success': False, 'message': 'data must be a non-empty string'}), 400

    current_email = current_user.get('email') if isinstance(current_user, dict) else None
    if not isinstance(current_email, str) or not current_email.strip():
        return jsonify({'success': False, 'message': 'Authenticated profile has no email'}), 403

    try:
        result = supabase.table('object_message').select('*').eq("main_user", current_email).execute()
        return jsonify({
            'success': True,
            'data': result.data
        }), 200
    except Exception:
        app.logger.exception("Conversation list fetch failed")
        return jsonify({
            'success': False,
            'message': 'Failed to fetch conversations'
        }), 500

# Example: Get user's map data (protected route)
@app.route('/api/getprodd', methods=['GET', 'POST'])
@token_required
def get_product(current_user):
    """Get a published marketplace listing by UUID."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': 'A JSON object is required'}), 400

    received_value = data.get('data')
    if not isinstance(received_value, str) or not received_value.strip():
        return jsonify({'success': False, 'message': 'A parcel UUID is required'}), 400

    try:
        result = (
            supabase.table('main_parcels')
            .select('uuid,owner,owner_uuid,parcel_name,description,price,currency,price_m2,location_name,sell_category,status_category')
            .eq('uuid', received_value)
            .eq('sell_category', 'to_sell')
            .execute()
        )
        if not result.data:
            return jsonify({'success': False, 'message': 'Marketplace listing not found'}), 404

        return jsonify({
            'success': True,
            'data': result.data
        }), 200

    except Exception:
        app.logger.exception("Marketplace product lookup failed")
        return jsonify({
            'success': False,
            'message': 'Failed to fetch marketplace listing'
        }), 500

# Example: Get user's map data (protected route)
@app.route('/api/market', methods=['GET', 'POST'])
@token_required
def get_market(current_user):
    """Get map data for authenticated user"""
    try:
        # Example: fetch locations from Supabase that belong to the user
        result = supabase.table('main_parcels').select('*').eq('sell_category', 'to_sell').execute() #changer pour la table profile
        #result = supabase.table('profiles').select('*').eq('email', current_user['email']).execute() #changer pour la table profile


        return jsonify({
            'success': True,
            'data': result.data
        }), 200

    except Exception:
        app.logger.exception("Marketplace map lookup failed")
        return jsonify({
            'success': False,
            'message': 'Failed to fetch marketplace data'
        }), 500

@app.route('/api/update_t', methods=['GET', 'POST'])
@token_required
def update_map_data(current_user):
    """Get map data for authenticated user"""
    owner_email = current_user.get('email') if isinstance(current_user, dict) else None
    if not isinstance(owner_email, str) or not owner_email.strip():
        return jsonify({'success': False, 'message': 'Authenticated profile has no email'}), 403

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': 'A JSON object is required'}), 400

    received_value = data.get('element')
    rowid = data.get('id')
    allowed_fields = {
        'parcel_name', 'location_name', 'description', 'price', 'price_m2',
        'currency', 'sell_category', 'status_category'
    }
    string_fields = {
        'parcel_name', 'location_name', 'description', 'currency',
        'sell_category', 'status_category'
    }
    numeric_fields = {'price', 'price_m2'}

    if not isinstance(received_value, dict) or not received_value:
        return jsonify({'success': False, 'message': 'element must be a non-empty object'}), 400
    if rowid is None or isinstance(rowid, bool) or not isinstance(rowid, (str, int)) or (isinstance(rowid, str) and not rowid.strip()):
        return jsonify({'success': False, 'message': 'A valid parcel id is required'}), 400
    if any(field not in allowed_fields for field in received_value):
        return jsonify({'success': False, 'message': 'element contains a non-editable field'}), 400

    for field, value in received_value.items():
        if field in string_fields and not isinstance(value, str):
            return jsonify({'success': False, 'message': f'{field} must be a string'}), 400
        if field in numeric_fields:
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                return jsonify({'success': False, 'message': f'{field} must be numeric'}), 400
            try:
                float(value)
            except (TypeError, ValueError, OverflowError):
                return jsonify({'success': False, 'message': f'{field} must be numeric'}), 400

    try:
        # Example: fetch locations from Supabase that belong to the user
        result = supabase.table('main_parcels').update(received_value).eq('id', rowid).eq('owner_uuid', owner_email).select('id').execute()
        if not result.data:
            return jsonify({'success': False, 'message': 'Parcel not found'}), 404
        #result = supabase.table('profiles').select('*').eq('email', current_user['email']).execute() #changer pour la table profile


        return jsonify({
            'success': True,
            'data': result.data
        }), 200

    except Exception:
        app.logger.exception("Parcel update failed")
        return jsonify({
            'success': False,
            'message': 'Failed to update parcel'
        }), 500
# Example: Get user's map data (protected route)

@app.route('/api/map-data', methods=['GET'])
@token_required
def get_map_data(current_user):
    """Get map data for authenticated user"""
    try:
        # Example: fetch locations from Supabase that belong to the user
        result = supabase.table('main_parcels').select('*').eq('owner_uuid', current_user['email']).execute() #changer pour la table profile
        #result = supabase.table('profiles').select('*').eq('email', current_user['email']).execute() #changer pour la table profile


        return jsonify({
            'success': True,
            'data': result.data
        }), 200

    except Exception:
        app.logger.exception("Parcel map lookup failed")
        return jsonify({
            'success': False,
            'message': 'Failed to fetch parcel map data'
        }), 500

# Example: Get user's map data (protected route)
@app.route('/api/account', methods=['GET', 'POST'])
@token_required
def get_map_account(current_user):
    """Get map data for authenticated user"""
    try:
        # Example: fetch locations from Supabase that belong to the user
        #result = supabase.table('main_parcels').select('*').eq('owner_uuid', current_user['email']).execute() #changer pour la table profile
        result = supabase.table('profiles').select('*').eq('email', current_user['email']).execute() #changer pour la table profile
        return jsonify({
            'success': True,
            'data': result.data
        }), 200

    except Exception:
        app.logger.exception("Account profile lookup failed")
        return jsonify({
            'success': False,
            'message': 'Failed to fetch account information'
        }), 500

@app.route('/api/logout', methods=['POST'])
@token_required
def logout(current_user):
    """Logout user"""
    try:
        # Supabase handles logout on the client side
        # You can invalidate the token on your backend if needed
        return jsonify({
            'success': True,
            'message': 'Logout successful'
        }), 200
    except Exception:
        app.logger.exception("Logout failed")
        return jsonify({
            'success': False,
            'message': 'Logout failed'
        }), 500

@app.route('/api/refresh-token', methods=['POST'])
def refresh_token():
    """Refresh access token using refresh token"""
    data = request.get_json(silent=True)
    refresh_token = data.get('refresh_token') if isinstance(data, dict) else None

    if not isinstance(refresh_token, str) or not refresh_token.strip():
        return jsonify({'success': False, 'message': 'Refresh token is missing or invalid'}), 401

    try:
        # Use Supabase to refresh the token
        auth_response = supabase.auth.refresh_session(refresh_token)
        session = getattr(auth_response, 'session', None)
        access_token = getattr(session, 'access_token', None)
        rotated_refresh_token = getattr(session, 'refresh_token', None)

        if access_token and rotated_refresh_token:
            return jsonify({
                'success': True,
                'message': 'Token refreshed',
                'token': access_token,
                'refresh_token': rotated_refresh_token
            }), 200
        return jsonify({'success': False, 'message': 'Refresh token is no longer valid'}), 401
    except Exception as error:
        provider_status = getattr(error, 'status', None)
        provider_error_name = getattr(error, 'name', error.__class__.__name__)
        if provider_status in (400, 401, 403) or provider_error_name == 'AuthSessionMissingError':
            return jsonify({'success': False, 'message': 'Refresh token is no longer valid'}), 401
        app.logger.warning('The authentication provider could not refresh the session.')
        return jsonify({'success': False, 'message': 'The authentication service is temporarily unavailable'}), 503

# Configure upload settings
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MiB
MAX_MULTIPART_OVERHEAD = 256 * 1024
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE + MAX_MULTIPART_OVERHEAD
ALLOWED_EXTENSIONS = {'kml'}
KML_NAMESPACE = 'http://www.opengis.net/kml/2.2'

def allowed_file(filename):
    return isinstance(filename, str) and '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

class InvalidKmlInput(ValueError):
    """An uploaded KML document or parcel geometry is invalid."""


def _request_too_large_response():
    return jsonify({
        'status': 'error',
        'message': 'Request exceeds the maximum allowed size'
    }), 413


@app.errorhandler(RequestEntityTooLarge)
def handle_request_entity_too_large(_error):
    return _request_too_large_response()


def _local_xml_name(element):
    tag = element.tag
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else ''


def _xml_namespace(element):
    tag = element.tag
    if not isinstance(tag, str) or not tag.startswith('{'):
        return None
    return tag[1:].split('}', 1)[0]


def _parse_kml_ring(coords_text):
    if not isinstance(coords_text, str) or not coords_text.strip():
        raise InvalidKmlInput('KML polygon ring is empty')

    positions = []
    for coordinate_text in coords_text.split():
        components = coordinate_text.split(',')
        if len(components) not in (2, 3):
            raise InvalidKmlInput('KML coordinate must contain longitude, latitude, and optional altitude')
        try:
            values = [float(component) for component in components]
        except (TypeError, ValueError, OverflowError) as error:
            raise InvalidKmlInput('KML coordinates must be numeric') from error

        if not all(math.isfinite(value) for value in values):
            raise InvalidKmlInput('KML coordinates must be finite')

        longitude, latitude = values[:2]
        if not -180 <= longitude <= 180:
            raise InvalidKmlInput('KML longitude is outside WGS84 bounds')
        if not -90 <= latitude <= 90:
            raise InvalidKmlInput('KML latitude is outside WGS84 bounds')
        positions.append([longitude, latitude])

    if len(positions) < 4:
        raise InvalidKmlInput('KML polygon ring must contain at least four positions')
    if positions[0] != positions[-1]:
        raise InvalidKmlInput('KML polygon ring must be closed')
    if len({tuple(position) for position in positions[:-1]}) < 3:
        raise InvalidKmlInput('KML polygon ring must contain at least three distinct vertices')

    return positions


def _validate_kml_geometry(geometry):
    try:
        parsed_geometry = shape(geometry)
    except (TypeError, ValueError, ShapelyError) as error:
        raise InvalidKmlInput('KML polygon geometry is invalid') from error

    if parsed_geometry.is_empty or not parsed_geometry.is_valid:
        raise InvalidKmlInput('KML polygon geometry is invalid')


def coordinates_to_geojson(coords_text, geometry_type):
    """Convert one KML polygon ring to validated GeoJSON."""
    if geometry_type != 'Polygon':
        raise InvalidKmlInput('Only polygon geometries are accepted')

    geometry = {
        'type': 'Polygon',
        'coordinates': [_parse_kml_ring(coords_text)]
    }
    _validate_kml_geometry(geometry)
    return geometry


def _parse_kml_boundary_ring(boundary):
    linear_rings = [child for child in boundary if _local_xml_name(child) == 'LinearRing']
    if len(linear_rings) != 1:
        raise InvalidKmlInput('KML polygon boundary must contain one LinearRing')

    coordinate_elements = [
        child for child in linear_rings[0]
        if _local_xml_name(child) == 'coordinates'
    ]
    if len(coordinate_elements) != 1:
        raise InvalidKmlInput('KML LinearRing must contain one coordinates element')
    return _parse_kml_ring(coordinate_elements[0].text)


def _parse_kml_polygon(polygon):
    outer_boundaries = [
        child for child in polygon if _local_xml_name(child) == 'outerBoundaryIs'
    ]
    inner_boundaries = [
        child for child in polygon if _local_xml_name(child) == 'innerBoundaryIs'
    ]
    if len(outer_boundaries) != 1:
        raise InvalidKmlInput('KML polygon must have exactly one outer boundary')

    coordinates = [_parse_kml_boundary_ring(outer_boundaries[0])]
    coordinates.extend(_parse_kml_boundary_ring(boundary) for boundary in inner_boundaries)
    geometry = {'type': 'Polygon', 'coordinates': coordinates}
    _validate_kml_geometry(geometry)
    return geometry


_UNSUPPORTED_KML_GEOMETRIES = {
    'Point', 'LineString', 'LinearRing', 'Model', 'Track', 'MultiTrack'
}


def _collect_kml_polygons(element):
    tag = _local_xml_name(element)
    if tag == 'Polygon':
        return [_parse_kml_polygon(element)]
    if tag in _UNSUPPORTED_KML_GEOMETRIES:
        raise InvalidKmlInput('Only Polygon geometries are accepted')
    if tag == 'MultiGeometry':
        children = list(element)
        if not children or any(
            _local_xml_name(child) not in {'Polygon', 'MultiGeometry'}
            for child in children
        ):
            raise InvalidKmlInput('KML MultiGeometry may contain only Polygon geometries')
        polygons = []
        for child in children:
            polygons.extend(_collect_kml_polygons(child))
        if not polygons:
            raise InvalidKmlInput('KML MultiGeometry contains no polygons')
        return polygons

    polygons = []
    for child in list(element):
        polygons.extend(_collect_kml_polygons(child))
    return polygons


def _reject_xml_declarations(file_content):
    # KML does not need DTD or custom entities. Ignore NUL bytes so UTF-16
    # encodings cannot bypass this check.
    normalized_content = file_content.replace(b'\x00', b'').upper()
    if b'<!DOCTYPE' in normalized_content or b'<!ENTITY' in normalized_content:
        raise InvalidKmlInput('DTD and entity declarations are not accepted')


def parse_kml_file(file_path):
    """Parse and validate all polygon geometries in a KML document."""
    try:
        tree = ET.parse(file_path)
    except ET.ParseError as error:
        raise InvalidKmlInput('KML contains invalid XML') from error

    root = tree.getroot()
    if (
        _local_xml_name(root) != 'kml'
        or _xml_namespace(root) != KML_NAMESPACE
    ):
        raise InvalidKmlInput('XML document is not a KML document')

    polygons = _collect_kml_polygons(root)
    if not polygons:
        raise InvalidKmlInput('KML document contains no polygon geometry')

    if len(polygons) == 1:
        return polygons[0]

    geometry = {
        'type': 'MultiPolygon',
        'coordinates': [polygon['coordinates'] for polygon in polygons]
    }
    _validate_kml_geometry(geometry)
    return geometry

def store_parcel_in_supabase(form_data, geojson_geometry):
    """Store parcel data with GeoJSON geometry in existing table"""
    try:
        # Assuming your table is named 'parcels' - change this to your actual table name
        table_name = 'main_parcels'

        # Prepare the record data
        record_data = {
            'owner': form_data['owner'],
            'owner_uuid': form_data['owner_uuid'],
            'parcel_name': form_data['parcel_name'],
            'location_name': form_data['location_name'],
            'description': form_data.get('description', ''),
            'price_m2': form_data.get('price_m2'),
            'price': form_data.get('price'),
            'currency' : form_data.get('currency'),
            'geojson': json.dumps(geojson_geometry),  # Store as JSON string
            'created_at': datetime.utcnow().isoformat(),
            #'updated_at': datetime.utcnow().isoformat()
            'sell_category' : form_data.get('sell_category'),
            'status_category' : form_data.get('status_category')
        }

        # Remove None values to avoid database issues
        record_data = {k: v for k, v in record_data.items() if v is not None}

        # Insert record into the table
        response = supabase.table(table_name).insert(record_data).execute()

        if hasattr(response, 'data') and response.data:
            return {
                'status': 'success',
                'record_id': response.data[0]['id'],
                'message': 'Parcel data stored successfully'
            }
        else:
            app.logger.error("Supabase parcel insert returned no data")
            return {
                'status': 'error',
                'message': 'Failed to store parcel'
            }

    except Exception:
        app.logger.exception("Parcel persistence failed")
        return {
            'status': 'error',
            'message': 'Failed to store parcel'
        }

@app.route('/api/upload-kml', methods=['POST'])
@token_required
def upload_kml(current_user):
    """Handle KML file upload with parcel information"""
    temp_file_path = None
    try:
        owner_email = current_user.get('email') if isinstance(current_user, dict) else None
        if not isinstance(owner_email, str) or not owner_email.strip():
            return jsonify({
                'status': 'error',
                'message': 'Authenticated profile has no email'
            }), 403

        # Check if file is present in request
        if 'kmlFile' not in request.files:
            return jsonify({
                'status': 'error',
                'message': 'No file provided'
            }), 400

        file = request.files['kmlFile']

        # Check if file is selected
        if file.filename == '':
            return jsonify({
                'status': 'error',
                'message': 'No file selected'
            }), 400

        # Check file extension
        if not allowed_file(file.filename):
            return jsonify({
                'status': 'error',
                'message': 'Invalid file type. Only KML files are allowed.'
            }), 400

        # Get form data
        form_data = {
            'owner': request.form.get('owner', '').strip(),
            'owner_uuid': owner_email,
            'parcel_name': request.form.get('parcel_name', '').strip(),
            'location_name': request.form.get('location_name', '').strip(),
            'description': request.form.get('description', '').strip(),
            'price_m2': request.form.get('price_m2', type=float),
            'price': request.form.get('price', type=float),
            'sell_category' : request.form.get('sell_category', '').strip(),
            'status_category': request.form.get('status_category', '').strip(),
            'currency': request.form.get('currency', '').strip()

        }

        # Validate required form fields
        required_fields = ['owner', 'parcel_name', 'location_name', 'sell_category', 'status_category']
        for field in required_fields:
            if not form_data[field]:
                return jsonify({
                    'status': 'error',
                    'message': f'{field.replace("_", " ").title()} is required'
                }), 400

        # Read file content
        file_content = file.stream.read(MAX_FILE_SIZE + 1)

        # Check file size
        if len(file_content) > MAX_FILE_SIZE:
            return jsonify({
                'status': 'error',
                'message': 'File size exceeds the 10 MiB limit'
            }), 413
        if not file_content:
            raise InvalidKmlInput('KML file is empty')

        _reject_xml_declarations(file_content)

        # Create temporary file for parsing
        with tempfile.NamedTemporaryFile(delete=False, suffix='.kml') as temp_file:
            temp_file_path = temp_file.name
            temp_file.write(file_content)

        # Parse and validate polygon geometry as GeoJSON.
        geojson_geometry = parse_kml_file(temp_file_path)

        if not geojson_geometry:
            raise InvalidKmlInput('KML file contains no valid polygon geometry')

        # Store data in Supabase table.
        result = store_parcel_in_supabase(form_data, geojson_geometry)

        if result['status'] == 'success':
            return jsonify({
                'status': 'success',
                'message': result['message'],
                'data': {
                    'record_id': result['record_id'],
                    'parcel_name': form_data['parcel_name'],
                    'geometry_type': geojson_geometry.get('type', 'Unknown')
                }
            }), 200

        app.logger.error("KML parcel persistence failed")
        return jsonify({
            'status': 'error',
            'message': 'Failed to store parcel'
        }), 500

    except RequestEntityTooLarge:
        return _request_too_large_response()
    except InvalidKmlInput as error:
        app.logger.warning("KML input rejected (%s)", type(error).__name__)
        return jsonify({
            'status': 'error',
            'message': 'Invalid KML file or geometry'
        }), 400

    except Exception:
        app.logger.exception("KML upload failed")
        return jsonify({
            'status': 'error',
            'message': 'Failed to upload parcel'
        }), 500
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
            except FileNotFoundError:
                pass
            except OSError as error:
                app.logger.error(
                    "KML temporary file cleanup failed (%s)",
                    type(error).__name__
                )

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({'status': 'healthy', 'message': 'Parcel Upload API is running'})

#export part
def geojson_to_kml(rows, name_field='name', desc_field='description'):
    placemarks = []
    for row in rows:
        geom_data = row['geojson']  # your actual column name

        # if stored as string, parse it
        if isinstance(geom_data, str):
            geom_data = geojson.loads(geom_data)

        geom = shape(geom_data)  # shapely geometry
        coords = geom_to_kml_coords(geom)
        name = row.get(name_field, '')
        desc = row.get(desc_field, '')

        placemarks.append(f"""
        <Placemark>
            <name>{name}</name>
            <description>{desc}</description>
            {coords}
        </Placemark>""")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Export</name>
    {''.join(placemarks)}
  </Document>
</kml>"""

def geom_to_kml_coords(geom):
    gtype = geom.geom_type
    if gtype == 'Point':
        x, y = geom.x, geom.y
        return f"<Point><coordinates>{x},{y},0</coordinates></Point>"
    elif gtype == 'LineString':
        coords = ' '.join(f"{x},{y},0" for x, y in geom.coords)
        return f"<LineString><coordinates>{coords}</coordinates></LineString>"
    elif gtype == 'Polygon':
        outer = ' '.join(f"{x},{y},0" for x, y in geom.exterior.coords)
        rings = f"<outerBoundaryIs><LinearRing><coordinates>{outer}</coordinates></LinearRing></outerBoundaryIs>"
        for interior in geom.interiors:
            inner = ' '.join(f"{x},{y},0" for x, y in interior.coords)
            rings += f"<innerBoundaryIs><LinearRing><coordinates>{inner}</coordinates></LinearRing></innerBoundaryIs>"
        return f"<Polygon>{rings}</Polygon>"
    elif gtype.startswith('Multi') or gtype == 'GeometryCollection':
        parts = ''.join(geom_to_kml_coords(g) for g in geom.geoms)
        return f"<MultiGeometry>{parts}</MultiGeometry>"
    return ''

@app.route('/export/kml', methods=['GET', 'POST'])
@token_required
def export_kml(current_user):
    response = supabase.table('main_parcels').select('*').eq('owner_uuid', current_user['email']).execute()
    kml = geojson_to_kml(response.data)

    return Response(
        kml,
        mimetype='application/vnd.google-earth.kml+xml',
        headers={'Content-Disposition': 'attachment; filename=export.kml'}
    )

##new adds ##

import requests
import math
import requests_cache
import time
from datetime import timezone
from email.utils import parsedate_to_datetime

EXTERNAL_API_TIMEOUT = (5, 10)  # connect timeout, read timeout (seconds)
EXTERNAL_API_CACHE_TTLS = {
    "OpenTopoData elevation": 30 * 24 * 60 * 60,
    "OpenTopoData slope": 30 * 24 * 60 * 60,
    "SoilGrids": 30 * 24 * 60 * 60,
    "flood-tany": 15 * 60,
    "tany-climate season": 24 * 60 * 60,
    "tany-climate proximity": 6 * 60 * 60,
}
EXTERNAL_API_RETRYABLE_STATUSES = {408, 429, 500, 502, 503, 504}
EXTERNAL_API_RETRY_DELAY = 0.25
EXTERNAL_API_MAX_RETRY_DELAY = 0.5


def _is_cacheable_external_response(response):
    """Cache only successful HTTP responses containing valid JSON."""
    if not 200 <= response.status_code < 300:
        return False
    try:
        response.json()
    except ValueError:
        return False
    return True


_external_api_session = requests_cache.CachedSession(
    cache_name=".external_api_cache",
    allowable_methods=("GET",),
    allowable_codes=tuple(range(200, 300)),
    filter_fn=_is_cacheable_external_response,
)


def _retry_delay(response=None):
    """Return the bounded retry delay, honoring a short Retry-After for 429."""
    if response is not None and response.status_code == 429:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                delay = float(retry_after)
            except (TypeError, ValueError):
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    delay = None

            if delay is not None and 0 <= delay <= EXTERNAL_API_MAX_RETRY_DELAY:
                return delay

    return EXTERNAL_API_RETRY_DELAY


def _consume_external_retry(retry_budget, service, response=None):
    if not isinstance(retry_budget, dict) or retry_budget.get("remaining", 0) <= 0:
        return False

    retry_budget["remaining"] -= 1
    delay = _retry_delay(response)
    app.logger.info("Retrying %s once after %.3f seconds", service, delay)
    if delay:
        time.sleep(delay)
    return True


def _get_external_json(url, service, params=None, ttl=None, retry_budget=None):
    """Fetch and decode external JSON with shared cache and retry controls."""
    while True:
        try:
            response = _external_api_session.get(
                url,
                params=params,
                timeout=EXTERNAL_API_TIMEOUT,
                expire_after=ttl,
            )
            response.raise_for_status()
            try:
                return response.json()
            except ValueError:
                app.logger.warning("%s returned invalid JSON", service)
                return None
        except (requests.Timeout, requests.ConnectionError) as error:
            if _consume_external_retry(retry_budget, service):
                continue
            app.logger.warning("%s request failed (%s)", service, type(error).__name__)
            return None
        except requests.HTTPError as error:
            response = error.response
            status_code = response.status_code if response is not None else None
            if (
                status_code in EXTERNAL_API_RETRYABLE_STATUSES
                and _consume_external_retry(retry_budget, service, response)
            ):
                continue
            app.logger.warning("%s returned HTTP status %s", service, status_code)
            return None
        except requests.RequestException as error:
            app.logger.warning("%s request failed (%s)", service, type(error).__name__)
            return None


def _is_finite_number(value):
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def get_terrain_slope(lat, lon, distance=30, retry_budget=None):
    # 1. Calculate the 8 surrounding points
    # 1 degree of lat approx 111,320 meters
    earth_radius = 6378137
    lat_rad = math.radians(lat)

    # Offsets for 8 directions (0, 45, 90, ..., 315 degrees)
    points = [(lat, lon)] # Start with center
    for i in range(8):
        angle = math.radians(i * 45)

        # Calculate coordinate offsets
        d_lat = (distance * math.cos(angle)) / 111320
        d_lon = (distance * math.sin(angle)) / (111320 * math.cos(lat_rad))

        points.append((lat + d_lat, lon + d_lon))

    # 2. Batch request to API
    # Format: loc1_lat,loc1_lon|loc2_lat,loc2_lon...
    locations_str = "|".join([f"{p[0]},{p[1]}" for p in points])
    url = f"https://api.opentopodata.org/v1/srtm90m?locations={locations_str}"

    response = _get_external_json(
        url,
        "OpenTopoData slope",
        ttl=EXTERNAL_API_CACHE_TTLS["OpenTopoData slope"],
        retry_budget=retry_budget,
    )
    results = response.get('results') if isinstance(response, dict) else None
    if not isinstance(results, list) or len(results) != len(points):
        app.logger.warning("OpenTopoData slope returned an incomplete result set")
        return None

    elevations = []
    for result in results:
        elevation = result.get('elevation') if isinstance(result, dict) else None
        if not _is_finite_number(elevation):
            app.logger.warning("OpenTopoData slope returned a missing or invalid elevation")
            return None
        elevations.append(elevation)

    center_elev = elevations[0]
    surrounding_elevs = elevations[1:]

    # 3. Calculate slopes
    # Slope = atan(rise / run) * (180 / pi)
    slopes = []
    for elev in surrounding_elevs:
        rise = elev - center_elev
        # Formula: degrees = atan(rise/run) * (180/pi)
        slope_deg = math.degrees(math.atan(abs(rise) / distance))
        slopes.append(slope_deg)

    return {
        "max_slope": max(slopes),
        "avg_slope": sum(slopes) / len(slopes),
        "individual_slopes": slopes
    }

def get_soil_data(longitude, latitude, retry_budget=None):
    """
    Fetches soil data from the ISRIC SoilGrids API and returns a flattened
    dictionary of {property_depth : mean_value}.
    """
    soil = []
    url = "https://rest.isric.org/soilgrids/v2.0/properties/query"

    params = {
        'lon': longitude,
        'lat': latitude,
        'property': ['bdod', 'cec', 'cfvo', 'clay', 'nitrogen', 'phh2o', 'sand', 'silt', 'soc', 'ocd', 'wv0010', 'wv0033', 'wv1500'],
        'depth': ['0-5cm'],
        'value': 'mean'
    }

    data = _get_external_json(
        url,
        "SoilGrids",
        params=params,
        ttl=EXTERNAL_API_CACHE_TTLS["SoilGrids"],
        retry_budget=retry_budget,
    )
    if not isinstance(data, dict):
        return None

    try:
        layers = data["properties"]["layers"]
        for layer in layers:
            soil.append({
                "name": layer["name"],
                "unit_measure": layer["unit_measure"]["mapped_units"],
                "values": layer["depths"][0]["values"]["mean"],
            })
        return soil
    except (KeyError, TypeError, IndexError):
        app.logger.warning("SoilGrids returned an incomplete response")
        return None

def get_info(xy):
    latitude = xy["latitude"]
    longitude = xy["longitude"]
    retry_budget = {"remaining": 1}
    value = {
        "elevation": None,
        "max_slope": None,
        "avg_slope": None,
        "individual_slopes": [],
        "soil_value": [],
        "flood": {
            "coordinate": {"latitude": latitude, "longitude": longitude},
            "flood_risk": None,
            "forecast": {"peak_discharge_m3s": None, "days_checked": None},
            "statistics": {"years_analyzed": None, "history_from": None, "thresholds_m3s": {}},
            "data_source": "GloFAS v4 via Open-Meteo",
            "assessed_on": None,
        },
        "climates": {},
        "proximity": {
            "query": {"lat": latitude, "lon": longitude, "radius_m": 5000},
            "results": {"hospital": None, "market": None, "transport": None},
        },
    }

    # Elevation
    open_topo_api = "https://api.opentopodata.org/v1/srtm90m?locations={},{}&interpolation=cubic&format=json".format(latitude, longitude)
    data = _get_external_json(
        open_topo_api,
        "OpenTopoData elevation",
        ttl=EXTERNAL_API_CACHE_TTLS["OpenTopoData elevation"],
        retry_budget=retry_budget,
    )
    results = data.get("results") if isinstance(data, dict) else None
    if isinstance(results, list) and results and isinstance(results[0], dict):
        elevation = results[0].get("elevation")
        if _is_finite_number(elevation):
            value["elevation"] = elevation
        else:
            app.logger.warning("OpenTopoData elevation returned a missing or invalid elevation")
    else:
        app.logger.warning("OpenTopoData elevation returned an incomplete response")

    # Slopes
    slopes = get_terrain_slope(latitude, longitude, 30, retry_budget=retry_budget)
    if isinstance(slopes, dict):
        value.update(slopes)

    # Soil properties
    soil = get_soil_data(longitude, latitude, retry_budget=retry_budget)
    if isinstance(soil, list):
        value["soil_value"] = soil

    # Flood risk
    flood = _get_external_json(
        "https://flood-tany.onrender.com/flood-risk",
        "flood-tany",
        params={"lat": latitude, "lon": longitude},
        ttl=EXTERNAL_API_CACHE_TTLS["flood-tany"],
        retry_budget=retry_budget,
    )
    flood_coordinate = flood.get("coordinate") if isinstance(flood, dict) else None
    if (
        isinstance(flood, dict)
        and not flood.get("error")
        and isinstance(flood_coordinate, dict)
        and _is_finite_number(flood_coordinate.get("latitude"))
        and _is_finite_number(flood_coordinate.get("longitude"))
        and isinstance(flood.get("forecast"), dict)
        and isinstance(flood.get("statistics"), dict)
    ):
        value["flood"] = flood
    elif flood is not None:
        app.logger.warning("flood-tany returned an incomplete response")

    # Climate. Query parameter names and coordinate values use the same axis.
    climates = _get_external_json(
        "https://tany-climate.onrender.com/api/season",
        "tany-climate season",
        params={"longitude": longitude, "latitude": latitude},
        ttl=EXTERNAL_API_CACHE_TTLS["tany-climate season"],
        retry_budget=retry_budget,
    )
    if isinstance(climates, dict) and not climates.get("error"):
        value["climates"] = climates
    elif climates is not None:
        app.logger.warning("tany-climate season returned an incomplete response")

    # Proximity
    proximity = _get_external_json(
        "https://tany-climate.onrender.com/api/proximity",
        "tany-climate proximity",
        params={"longitude": longitude, "latitude": latitude},
        ttl=EXTERNAL_API_CACHE_TTLS["tany-climate proximity"],
        retry_budget=retry_budget,
    )
    proximity_results = proximity.get("results") if isinstance(proximity, dict) else None
    if (
        isinstance(proximity, dict)
        and not proximity.get("error")
        and isinstance(proximity.get("query"), dict)
        and isinstance(proximity_results, dict)
        and all(isinstance(proximity_results.get(category), list) for category in ("hospital", "market", "transport"))
    ):
        value["proximity"] = proximity
    elif proximity is not None:
        app.logger.warning("tany-climate proximity returned an incomplete response")

    return value


'''add for statics in pythonanywhere: take data from supabase directly instead of fetching the api'''
@app.route('/api/get_infos', methods=['GET', 'POST'])
@token_required
def great_info(current_user):
    if current_user.get("account") != "pro":
        return jsonify({
            'success': False,
            'message': 'A Pro account is required to access this GeoReport'
        }), 403

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': 'A JSON object is required'}), 400

    uuid_p = data.get("data")
    if not isinstance(uuid_p, str) or not uuid_p.strip():
        return jsonify({'success': False, 'message': 'A parcel UUID is required'}), 400

    try:
        parcel = (
            supabase.table('main_parcels')
            .select('uuid')
            .eq('uuid', uuid_p)
            .eq('sell_category', 'to_sell')
            .execute()
        )
        if not parcel.data:
            return jsonify({'success': False, 'message': 'Marketplace listing not found'}), 404

        result = supabase.table('georeport').select('*').eq("uuid_parcel", uuid_p).execute()
        return jsonify({
            'success': True,
            'data': result.data
        }), 200
    except Exception:
        app.logger.exception("Stored GeoReport lookup failed")
        return jsonify({
            'success': False,
            'message': 'Failed to fetch GeoReport'
        }), 500

'''end of the new fetching '''

@app.route('/api/get_infos_', methods=['GET', 'POST', 'OPTIONS'])
@token_required
def give_info(current_user):
    if current_user.get("account") != "pro":
        return jsonify({
            'success': False,
            'message': 'A Pro account is required to access this GeoReport'
        }), 403

    data = request.get_json() #format {"longitude": xx.xx , "latitude" : xx.xx}
    try:
        results = get_info(data.get("data"))
        return jsonify({
                'success': True,
                'data': results,
                'balise' : data.get("data")
            }), 200
    except Exception:
        app.logger.exception("GeoReport calculation failed")
        return jsonify({
            'success': False,
            'message': 'Failed to fetch map data'
        }), 500


###news###
@app.route('/api/getcoords', methods=['GET', 'POST', 'OPTIONS'])
@token_required
def get_coords(current_user):
    """Deprecated endpoint retained temporarily for compatibility."""
    return _deprecated_getcoords_response()

if __name__ == '__main__':
    debug = os.getenv("FLASK_DEBUG", "").lower() == "true"
    app.run(debug=debug, port=5000)
