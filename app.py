from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_mysqldb import MySQL
import bcrypt
import json
import re # Import regular expressions library for parsing
import google.generativeai as genai
import requests # Import requests for link validation
import os
import time # Import the time module for handling delays
import cloudinary
import cloudinary.uploader
import cloudinary.api

app = Flask(__name__)
# IMPORTANT: Use a long, random, and secret key in a real application
app.secret_key = 'your_very_long_and_random_secret_key' 

# --- Configure Gemini API ---
genai.configure(api_key="YOUR_API_KEY") 
model = genai.GenerativeModel('gemini-2.5-pro')

# --- Configure Cloudinary ---
cloudinary.config( 
  cloud_name = "YOUR_CLOUDINARY_NODE_NAME", 
  api_key = "YOUR_KEY", 
  api_secret = "YOUR_SECRET",
  secure = True
)

# Configure MySQL
app.config['MYSQL_HOST'] = 'localhost'
app.config['MYSQL_USER'] = 'root'
app.config['MYSQL_PASSWORD'] = 'YOUR_SQL_PASSWORD'
app.config['MYSQL_DB'] = 'YOUR_DB_NAME'
# Use DictCursor to get results as dictionaries, which is easier to work with
app.config['MYSQL_CURSORCLASS'] = 'DictCursor' 

mysql = MySQL(app)

# ----------------- HELPER FUNCTION: VALIDATE LINKS -----------------
def validate_and_clean_links(parsed_data):
    """
    Iterates through the parsed curriculum data, validates each URL, 
    and replaces broken links with a helpful search link.
    """
    for path_key in ["Short Duration Path", "Moderate Duration Path", "Long Duration Path"]:
        if path_key in parsed_data:
            for phase in parsed_data[path_key]:
                for step in phase.get("Steps", []):
                    courses_to_process = step.get("Courses", [step])
                    for course in courses_to_process:
                        for link_key in ["Course Link", "Alternative Free Link"]:
                            if link_key in course and course[link_key]:
                                url = course[link_key]
                                is_valid = False
                                try:
                                    # Use a HEAD request for efficiency, with a timeout
                                    response = requests.head(url, allow_redirects=True, timeout=5)
                                    # Consider any 2xx or 3xx status code as valid
                                    if 200 <= response.status_code < 400:
                                        is_valid = True
                                        # Update the URL to the final destination after redirects
                                        course[link_key] = response.url
                                except requests.RequestException:
                                    is_valid = False
                                
                                if not is_valid:
                                    print(f"BROKEN LINK FOUND: {url}. Replacing with search link.")
                                    course_name = course.get("Course Name", "course")
                                    search_query = "+".join(course_name.split())
                                    course[link_key] = f"https://www.google.com/search?q={search_query}"

    return parsed_data

# ----------------- HELPER FUNCTION: PARSE LLM RESPONSE (JSON DIRECT + PROVIDER EXTRACTION) -----------------
def parse_gemini_response_to_json(text_response):
    """
    Parses the JSON string from the LLM, then cleans up the markdown links 
    and extracts the course provider within the data.
    """
    print("--- Raw Gemini Response Received ---")
    print(text_response)
    print("------------------------------------")
    
    try:
        # Clean the response by removing markdown code block fences if they exist
        cleaned_text = re.sub(r'^```json\s*|```\s*$', '', text_response, flags=re.MULTILINE).strip()
        
        # Directly load the cleaned string into a Python dictionary
        parsed_data = json.loads(cleaned_text)

        # --- NEW: Loop through the data to clean up links and add provider ---
        for path_key in ["Short Duration Path", "Moderate Duration Path", "Long Duration Path"]:
            if path_key in parsed_data:
                for phase in parsed_data[path_key]:
                    for step in phase.get("Steps", []):
                        # This handles the case where a step might have multiple courses
                        courses_to_process = step.get("Courses", [step])
                        for course in courses_to_process:
                            # Clean the main course link
                            if "Course Link" in course:
                                link_text = course["Course Link"]
                                link_match = re.search(r'\[(.*?)\]\((.*?)\)', link_text)
                                if link_match:
                                    course_name_full = link_match.group(1)
                                    course["Course Link"] = link_match.group(2)

                                    ''' # Extract Provider from the course name
                                    provider_match = re.search(r'\((.*?)\)', course_name_full)
                                    if provider_match:
                                        course["Provider"] = provider_match.group(1).strip()
                                    else:
                                        course["Provider"] = "Web" # Default '''
                            
                            # Clean the alternative free link if it exists
                            if "Alternative Free Link" in course and course["Alternative Free Link"]:
                                alt_link_text = course["Alternative Free Link"]
                                alt_link_match = re.search(r'\[.*?\]\((.*?)\)', alt_link_text)
                                if alt_link_match:
                                    course["Alternative Free Link"] = alt_link_match.group(1)

        return parsed_data

    except json.JSONDecodeError as e:
        print(f"FATAL ERROR: Could not decode JSON from LLM response. Error: {e}")
        return {"Title": "Error: Could Not Parse Curriculum", "Short Duration Path": [], "Moderate Duration Path": [], "Long Duration Path": []}
    except Exception as e:
        print(f"An unexpected error occurred during parsing: {e}")
        return {"Title": "Error: An Unexpected Error Occurred", "Short Duration Path": [], "Moderate Duration Path": [], "Long Duration Path": []}




# --------------------- ROUTES ---------------------

@app.route('/')
def home():
    return render_template('LandingPageUltimate.html')

@app.route('/login')
def login_page():
    message = session.pop('message', None)
    return render_template('LoginPage.html', message=message)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))

@app.route('/signup')
def signup_page():
    return render_template('SignUpPage.html')

@app.route('/dashboard')
def dashboard_page():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))
    
    user_id = session['user_id']
    username = session.get('username', 'User')
    message = session.pop('message', None)
    prefilled_prompt = session.pop('landing_prompt', None)
    
    cur = mysql.connection.cursor()
    cur.execute("SELECT id, goal_prompt, curriculum_response FROM goals WHERE user_id = %s ORDER BY created_at DESC", (user_id,))
    past_goals = cur.fetchall()
    cur.close()

    for goal in past_goals:
        if goal['curriculum_response']:
            try:
                response_data = json.loads(goal['curriculum_response'])
                # The response is stored as JSON, so we can directly access keys
                goal['title'] = response_data.get('Title', 'Untitled Curriculum')
            except (json.JSONDecodeError, TypeError): # Handles if it's not a dict
                goal['title'] = 'View Curriculum'
        else:
            goal['title'] = 'Processing...'

    return render_template('DashBoard.html', 
                           message=message, 
                           username=username, 
                           prefilled_prompt=prefilled_prompt,
                           past_goals=past_goals)

@app.route('/curriculum/<int:goal_id>')
def curriculum_page(goal_id):
    if 'user_id' not in session:
        return redirect(url_for('login_page'))

    cur = mysql.connection.cursor()
    cur.execute("SELECT curriculum_response FROM goals WHERE id = %s AND user_id = %s", (goal_id, session['user_id']))
    goal = cur.fetchone()
    cur.close()

    if not goal or not goal['curriculum_response']:
        return "Curriculum not found or is still being generated.", 404

    # The response from the DB is a JSON string. We must parse it into a Python dict.    
    try:
        curriculum_data = json.loads(goal['curriculum_response'])
    except (json.JSONDecodeError, TypeError):
        # Handle cases where the data is malformed or not a string
        curriculum_data = {"Title": "Error: Could not load curriculum data."}
    
    return render_template('CurriculumPage.html', curriculum_data=curriculum_data, goal_id=goal_id)

@app.route('/handle_landing_prompt', methods=['POST'])
def handle_landing_prompt():
    prompt = request.form.get('prompt')
    if prompt:
        session['landing_prompt'] = prompt
    session['message'] = 'Please log-in first'
    return redirect(url_for('login_page'))

# ----------------- API: Email Check -----------------

@app.route('/check_email', methods=['POST'])
def check_email():
    email = request.json['email']
    cur = mysql.connection.cursor()
    cur.execute("SELECT email FROM users WHERE email = %s", (email,))
    user = cur.fetchone()
    cur.close()
    return jsonify({'exists': bool(user)})

# ----------------- API: Sign-Up -----------------

@app.route('/signup_user', methods=['POST'])
def signup_user():
    email = request.form['email']
    password = request.form['password']

    cur = mysql.connection.cursor()
    cur.execute("SELECT email FROM users WHERE email = %s", (email,))
    user = cur.fetchone()

    if user:
        return render_template('LoginPage.html', message='Email already exists. Please log in.')

    hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
    cur.execute("INSERT INTO users (email, password) VALUES (%s, %s)", (email, hashed_password))
    mysql.connection.commit()
    cur.close()

    return render_template('LoginPage.html', message='User registered successfully. Please log in.')

# ----------------- API: Login -----------------

@app.route('/login_user', methods=['POST'])
def login_user():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'Request must be JSON'}), 400

        email = data.get('email')
        password = data.get('password')

        cur = mysql.connection.cursor()
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        cur.close()

        if not user:
            return jsonify({'success': False, 'message': 'No account found. Please sign up.'})

        # Use the column name 'password' thanks to DictCursor
        hashed_pw = user['password'].encode('utf-8')
        if bcrypt.checkpw(password.encode('utf-8'), hashed_pw):
            # Store essential user info in the session
            session['user_id'] = user['id']
            session['username'] = email.split('@')[0].capitalize()
            session['message'] = 'Login successful!'
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'message': 'Incorrect Password.'})

    except Exception as e:
        return jsonify({'success': False, 'message': f'Server error: {str(e)}'}), 500

# ----------------- API: Save Goal and Generate Curriculum -----------------
@app.route('/api/save_and_generate', methods=['POST'])
def save_and_generate():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'User not logged in'}), 401

    try:
        data = request.get_json()
        user_id = session['user_id']
        
        main_prompt = data['mainPrompt']
        responses = data['questionnaireResponses']
        final_llm_prompt = data['finalPromptForLLM']

        # --- 1. Save initial data to get a goal_id ---
        cur = mysql.connection.cursor()
        cur.execute("INSERT INTO goals (user_id, goal_prompt) VALUES (%s, %s)", (user_id, main_prompt))
        goal_id = cur.lastrowid
        
        # (Save profile, details, domains, subdomains as before)
        cur.execute("SELECT id FROM user_profiles WHERE user_id = %s", (user_id,))
        profile = cur.fetchone()
        if profile:
            cur.execute("UPDATE user_profiles SET user_type = %s, education_level = %s WHERE user_id = %s", (responses['general'], responses['level'], user_id))
        else:
            cur.execute("INSERT INTO user_profiles (user_id, user_type, education_level) VALUES (%s, %s, %s)", (user_id, responses['general'], responses['level']))
        duration = ", ".join(responses['duration'])
        cur.execute("INSERT INTO goal_details (goal_id, preferred_duration, reason) VALUES (%s, %s, %s)", (goal_id, duration, responses['reason']))
        for domain in responses['domain']:
            cur.execute("INSERT INTO goal_domains (goal_id, domain_name) VALUES (%s, %s)", (goal_id, domain))
            if domain in responses['subdomains'] and responses['subdomains'][domain]:
                for subdomain in responses['subdomains'][domain]:
                    cur.execute("INSERT INTO goal_subdomains (goal_id, domain_name, subdomain_name) VALUES (%s, %s, %s)", (goal_id, domain, subdomain))
        
        mysql.connection.commit() # Commit initial data

        # --- 2. Call Gemini API ---
        response = model.generate_content(final_llm_prompt)
        raw_response_text = response.text
        
        # --- 3. Parse the response and save the structured JSON to DB ---
        parsed_curriculum = parse_gemini_response_to_json(raw_response_text)
        
        # NOTE: The column type in MySQL should be JSON for this to work best
        cur.execute("UPDATE goals SET curriculum_response = %s WHERE id = %s", (json.dumps(parsed_curriculum), goal_id))
        mysql.connection.commit()
        cur.close()
        
        # --- 4. Return the URL for redirection ---
        return jsonify({'success': True, 'redirect_url': url_for('curriculum_page', goal_id=goal_id)})

    except Exception as e:
        print(f"Error in save_and_generate: {str(e)}")
        return jsonify({'success': False, 'message': f'An error occurred: {str(e)}'}), 500


# --- API for Uploading Proof ---
@app.route('/api/upload_proof', methods=['POST'])
def upload_proof():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'User not logged in'}), 401
    
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'message': 'No file part'}), 400
        
        file_to_upload = request.files['file']
        goal_id = request.form.get('goal_id')
        step_id = request.form.get('step_id')
        user_id = session['user_id']

        if file_to_upload.filename == '':
            return jsonify({'success': False, 'message': 'No selected file'}), 400

        # Upload to Cloudinary
        upload_result = cloudinary.uploader.upload(file_to_upload)
        secure_url = upload_result['secure_url']

        # Save the URL to the database
        cur = mysql.connection.cursor()
        # Use INSERT ... ON DUPLICATE KEY UPDATE to handle re-uploads
        cur.execute("""
            INSERT INTO user_progress (user_id, goal_id, step_id, proof_image_url)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE proof_image_url = %s
        """, (user_id, goal_id, step_id, secure_url, secure_url))
        mysql.connection.commit()
        cur.close()

        return jsonify({'success': True, 'url': secure_url})

    except Exception as e:
        print(f"Upload error: {e}")
        return jsonify({'success': False, 'message': 'Server error during upload'}), 500


# --- API for Getting Progress ---
@app.route('/api/get_progress/<int:goal_id>')
def get_progress(goal_id):
    if 'user_id' not in session:
        return jsonify({}), 401
    
    cur = mysql.connection.cursor()
    cur.execute("SELECT step_id, proof_image_url FROM user_progress WHERE user_id = %s AND goal_id = %s", (session['user_id'], goal_id))
    progress = cur.fetchall()
    cur.close()
    
    # Convert list of dicts to a single dict for easier lookup in JS
    progress_map = {item['step_id']: item['proof_image_url'] for item in progress}
    return jsonify(progress_map)


if __name__ == '__main__':
    app.run(debug=True)
