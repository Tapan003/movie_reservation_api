from flask import Flask, request, jsonify
from sqlalchemy.pool import NullPool
from flask_sqlalchemy import SQLAlchemy 
from werkzeug.security import generate_password_hash, check_password_hash
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from datetime import datetime
from payment_service import MockPaymentGateway
from flask_socketio import SocketIO, emit
from textblob import TextBlob
from datetime import datetime
import os

app = Flask(__name__)

# 1. Configure the Database
# This tells Flask to create a file named 'movies.db' in the current folder
# "Check if there is an environment variable called DATABASE_URL."
# "If yes (Cloud), use that. If no (Local), use sqlite:///movies.db"
database_url = os.environ.get('DATABASE_URL', 'sqlite:///movies.db')

# Fix for Render's Postgres URL (Render uses 'postgres://' but SQLAlchemy needs 'postgresql://')
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False # Turns off a warning message
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {"poolclass": NullPool}
app.config["JWT_SECRET_KEY"] = "super-secret-key"  # Change this in production!
socketio = SocketIO(app, cors_allowed_origins="*")
jwt = JWTManager(app)

# 2. Initialize the Database
db = SQLAlchemy(app)

# --- TEMPORARY DATABASE SETUP ENDPOINT ---
@app.route('/setup-db')
def setup_database():
    try:
        with app.app_context():
            # 1. Drop everything (Delete old tables with wrong limits)
            db.drop_all()
            # 2. Create everything new (With the new 256 limit)
            db.create_all()
        return "Database reset and created successfully! Old data is gone."
    except Exception as e:
        return f"Error: {str(e)}"
# -----------------------------------------

# 3. Define the Movie Model
# This class represents the 'movies' table in our database
class Movie(db.Model):
    id = db.Column(db.Integer, primary_key=True) # Unique ID for every movie
    title = db.Column(db.String(100), nullable=False) # Title, max 100 chars, cannot be empty
    director = db.Column(db.String(100), nullable=False)
    rating = db.Column(db.Float, nullable=True) # e.g., 8.5

    # This is a helper method to print the movie nicely later
    def __repr__(self):
        return f'<Movie {self.title}>'
    
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False) # unique=True means no duplicate usernames
    password_hash = db.Column(db.String(512), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
class Theater(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    location = db.Column(db.String(50), nullable=False)

class Showtime(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    show_time = db.Column(db.DateTime, nullable=False)
    price = db.Column(db.Float, nullable=False, default=10.0)
    
    # Foreign Keys: These link to the other tables
    movie_id = db.Column(db.Integer, db.ForeignKey('movie.id'), nullable=False)
    theater_id = db.Column(db.Integer, db.ForeignKey('theater.id'), nullable=False)
    
    # Relationships: These help us access the related objects easily in Python
    # e.g., showtime.movie.title
    movie = db.relationship('Movie', backref=db.backref('showtimes', lazy=True))
    theater = db.relationship('Theater', backref=db.backref('showtimes', lazy=True))

class Seat(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    row = db.Column(db.String(5), nullable=False)   # e.g., "A"
    number = db.Column(db.Integer, nullable=False)  # e.g., 1
    code = db.Column(db.String(10), nullable=False) # e.g., "A1" (Helper column)
    
    # Foreign Key
    theater_id = db.Column(db.Integer, db.ForeignKey('theater.id'), nullable=False)
    theater = db.relationship('Theater', backref=db.backref('seats', lazy=True))

    def __repr__(self):
        return f'<Seat {self.code}>'

class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    seat_code = db.Column(db.String(10), nullable=False) # e.g., "A1"
    
    # Foreign Keys
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    showtime_id = db.Column(db.Integer, db.ForeignKey('showtime.id'), nullable=False)
    
    # Relationships
    user = db.relationship('User', backref=db.backref('bookings', lazy=True))
    showtime = db.relationship('Showtime', backref=db.backref('bookings', lazy=True))

class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False) # The actual review
    rating = db.Column(db.Integer, nullable=False) # 1-10 User Rating
    
    # NLP Data (We calculate this automatically!)
    sentiment_score = db.Column(db.Float, nullable=True) # -1.0 to +1.0
    
    # Relationships
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    movie_id = db.Column(db.Integer, db.ForeignKey('movie.id'), nullable=False)
    
    user = db.relationship('User', backref=db.backref('reviews', lazy=True))
    movie = db.relationship('Movie', backref=db.backref('reviews', lazy=True))

@app.route('/')
def home():
    return "Hello! The Movie Reservation API is running."

# Endpoint: Initialize Theater Seats (Admin)
@app.route('/theaters/<int:theater_id>/seats', methods=['POST'])
@jwt_required()
def add_seats_to_theater(theater_id):
    # Data: { "rows": ["A", "B"], "seats_per_row": 5 }
    data = request.get_json()
    theater = Theater.query.get_or_404(theater_id)
    
    created_seats = []
    for row in data['rows']:
        for num in range(1, data['seats_per_row'] + 1):
            code = f"{row}{num}" # A1, A2...
            new_seat = Seat(
                row=row,
                number=num,
                code=code,
                theater_id=theater.id
            )
            db.session.add(new_seat)
            created_seats.append(code)
            
    db.session.commit()
    return jsonify({'message': f'Created {len(created_seats)} seats for {theater.name}', 'seats': created_seats}), 201

# Endpoint 1: CREATE a new movie
@app.route('/movies', methods=['POST'])
@jwt_required()
def add_movie():
    # 1. Get the data sent by the user (in JSON format)
    data = request.get_json()

    # 2. Create a new Movie object using that data
    new_movie = Movie(
        title=data['title'],
        director=data['director'],
        rating=data['rating']
    )

    # 3. Add to the database and save (commit)
    db.session.add(new_movie)
    db.session.commit()

    # 4. Return a success message
    return jsonify({'message': 'Movie added successfully!'}), 201

# Endpoint 2: READ all movies
@app.route('/movies', methods=['GET'])
def get_movies():
    # 1. Query the database for all movies
    movies = Movie.query.all()
    
    # 2. Convert the list of objects into a list of dictionaries (JSON)
    output = []
    for movie in movies:
        movie_data = {
            'id': movie.id,
            'title': movie.title,
            'director': movie.director,
            'rating': movie.rating
        }
        output.append(movie_data)

    # 3. Return the list
    return jsonify({'movies': output})

# Endpoint 3: Register a new user
@app.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    
    # Check if username already exists
    existing_user = User.query.filter_by(username=data['username']).first()
    if existing_user:
        return jsonify({'message': 'Username already exists'}), 400

    # Create new user
    new_user = User(username=data['username'])
    new_user.set_password(data['password']) # This hashes the password!

    db.session.add(new_user)
    db.session.commit()

    return jsonify({'message': 'User registered successfully!'}), 201

# Endpoint 4: Login
@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    
    # 1. Find the user by username
    user = User.query.filter_by(username=data.get('username')).first()

    # 2. Check if user exists AND password is correct
    if user and user.check_password(data.get('password')):
        # 3. Create a new token
        access_token = create_access_token(identity=str(user.id))
        return jsonify({'access_token': access_token}), 200
    
    return jsonify({'message': 'Invalid credentials'}), 401

# Endpoint 5: Create Theater (Protected)
@app.route('/theaters', methods=['POST'])
@jwt_required()
def create_theater():
    data = request.get_json()
    new_theater = Theater(name=data['name'], location=data['location'])
    db.session.add(new_theater)
    db.session.commit()
    return jsonify({'message': 'Theater created!'}), 201

# Endpoint 6: Create Showtime (Protected)
@app.route('/showtimes', methods=['POST'])
@jwt_required()
def create_showtime():
    data = request.get_json()
    
    # Convert string date "2024-12-01 18:30" into a Python datetime object
    date_obj = datetime.strptime(data['show_time'], '%Y-%m-%d %H:%M')
    
    new_showtime = Showtime(
        show_time=date_obj,
        movie_id=data['movie_id'],
        theater_id=data['theater_id'],
        price=data.get('price', 10.0)
    )
    db.session.add(new_showtime)
    db.session.commit()
    return jsonify({'message': 'Showtime created!'}), 201

# Endpoint 7: Get Showtimes for a specific Movie (Public)
@app.route('/movies/<int:movie_id>/showtimes', methods=['GET'])
def get_movie_showtimes(movie_id):
    # 1. Find the movie first (just to make sure it exists)
    movie = Movie.query.get_or_404(movie_id)

    # 2. Find all showtimes for this movie
    # We can access 'movie.showtimes' automatically because of the relationship we defined!
    showtimes = movie.showtimes 
    
    output = []
    for show in showtimes:
        output.append({
            'showtime_id': show.id,
            'time': show.show_time.strftime('%Y-%m-%d %H:%M'), # Format the date nicely
            'theater': show.theater.name, # <--- Magic! accessing the related theater name
            'location': show.theater.location,
            'price':show.price
        })
    
    return jsonify({
        'movie': movie.title,
        'showtimes': output
    })

# Endpoint 8: Book a Ticket (Protected & Paid)
@app.route('/bookings', methods=['POST'])
@jwt_required()
def book_ticket():
    current_user_id = get_jwt_identity()
    data = request.get_json()

    # Fetch the specific showtime to get its price
    showtime = Showtime.query.get(data['showtime_id'])
    
    # Check if showtime exists (good safety practice)
    if not showtime:
        return jsonify({'message': 'Showtime not found'}), 404

    # Does this seat exist in this theater?
    seat_exists = Seat.query.filter_by(
        theater_id=showtime.theater_id, 
        code=data['seat_code']
    ).first()

    if not seat_exists:
        return jsonify({'message': f"Seat {data['seat_code']} does not exist in this theater!"}), 400
    
    # 1. Validation: Check if seat is free
    existing_booking = Booking.query.filter_by(
        showtime_id=data['showtime_id'], 
        seat_code=data['seat_code']
    ).first()
    
    if existing_booking:
        return jsonify({'message': 'Sorry, that seat is already booked!'}), 400
    
    # 2. PAYMENT PROCESSING (The New Step)
    # We expect the user to send 'card_details' in the JSON body
    card_info = data.get('card_details', {})

    price = showtime.price
    
    payment_response = MockPaymentGateway.process_payment(card_info, price)
    
    if not payment_response['success']:
        # If payment fails, STOP. Do not book the seat.
        return jsonify({
            'message': 'Payment Failed',
            'error': payment_response['error']
        }), 400

    # 3. Create the booking (Only if payment worked)
    new_booking = Booking(
        user_id=current_user_id,
        showtime_id=data['showtime_id'],
        seat_code=data['seat_code']
        # Ideally, we would also save the payment_response['transaction_id'] here!
    )
    
    db.session.add(new_booking)
    db.session.commit()

    # BROADCAST UPDATE
    # "broadcast=True" means send to everyone, not just the person who booked
    socketio.emit('seat_booked', {
        'showtime_id': data['showtime_id'],
        'seat_code': data['seat_code']
    })
    
    return jsonify({
        'message': 'Booking confirmed!', 
        'booking_id': new_booking.id,
        'transaction_id': payment_response['transaction_id'] # Send receipt to user
    }), 201

# Endpoint: Post a Movie Review (Analyzed by AI)
@app.route('/reviews', methods=['POST'])
@jwt_required()
def add_review():
    current_user_id = get_jwt_identity()
    data = request.get_json()
    
    # 1. Run NLP Analysis
    # We pass the text into TextBlob
    blob = TextBlob(data['text'])
    
    # .sentiment.polarity returns a float between -1.0 (Negative) and 1.0 (Positive)
    polarity = blob.sentiment.polarity
    
    # 2. Create the Review Object
    new_review = Review(
        user_id=current_user_id,
        movie_id=data['movie_id'],
        text=data['text'],
        rating=data['rating'],
        sentiment_score=polarity # <--- Saving the AI score
    )
    
    db.session.add(new_review)
    db.session.commit()
    
    # 3. Return the result (Fun to see what the AI thought!)
    return jsonify({
        'message': 'Review added!',
        'sentiment_analysis': {
            'score': polarity,
            'verdict': 'Positive' if polarity > 0 else 'Negative'
        }
    }), 201

if __name__ == '__main__':
    socketio.run(app, debug=True)