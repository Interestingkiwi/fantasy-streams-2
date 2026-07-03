"""
app.py for Fantasy Streams
Author - Jason Druckenmiller
Created - 7/3/2026
Updated - 7/3/2026
"""


from flask import Flask
from routes.main_routes import main_bp
from routes.auth_routes import auth_bp
from routes.draft_routes import draft_bp

app = Flask(__name__)

#Blueprints
app.register_blueprint(main_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(draft_bp)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
