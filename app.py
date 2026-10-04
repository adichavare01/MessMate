from flask import Flask, render_template, request, redirect, url_for, session
import sqlite3
from datetime import datetime, date
import qrcode
import os
import uuid

app = Flask(__name__)
app.secret_key = "messmate_secret_key"
DATABASE = "messmate.db"


def get_db():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    connection = get_db()

    connection.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS meals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meal_type TEXT NOT NULL,
            meal_date TEXT NOT NULL,
            menu TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            deadline TEXT NOT NULL
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS meal_responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            meal_id INTEGER NOT NULL,
            response TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, meal_id)
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            meal_id INTEGER NOT NULL,
            attendance_date TEXT NOT NULL,
            status TEXT NOT NULL,
            scan_time TEXT NOT NULL,
            UNIQUE(user_id, meal_id)
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            setting_name TEXT UNIQUE NOT NULL,
            setting_value TEXT NOT NULL
        )
    """)

    try:
        connection.execute("ALTER TABLE users ADD COLUMN student_id TEXT")
    except sqlite3.OperationalError:
        pass

    students = connection.execute(
        "SELECT id FROM users WHERE role = 'student' AND (student_id IS NULL OR student_id = '') ORDER BY id"
    ).fetchall()

    for student in students:
        student_id = f"MM{student['id']:03d}"
        connection.execute(
            "UPDATE users SET student_id = ? WHERE id = ?",
            (student_id, student["id"])
        )

    connection.commit()
    connection.close()


@app.route("/")
def home():
    if "user_id" in session:
        if session["role"] == "student":
            return redirect(url_for("student_dashboard"))
        return redirect(url_for("owner_dashboard"))

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        role = request.form["role"]

        connection = get_db()

        try:
            if role == "student":
                connection.execute(
                    """
                    INSERT INTO users
                    (name, email, password, role, student_id)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (name, email, password, role, None)
                )
            else:
                connection.execute(
                    """
                    INSERT INTO users
                    (name, email, password, role)
                    VALUES (?, ?, ?, ?)
                    """,
                    (name, email, password, role)
                )

            connection.commit()

            if role == "student":
                new_user = connection.execute(
                    "SELECT id FROM users WHERE email = ?",
                    (email,)
                ).fetchone()

                student_id = f"MM{new_user['id']:03d}"

                connection.execute(
                    "UPDATE users SET student_id = ? WHERE id = ?",
                    (student_id, new_user["id"])
                )

                connection.commit()

            connection.close()
            return redirect(url_for("login"))

        except sqlite3.IntegrityError:
            connection.close()
            return render_template(
                "register.html",
                error="Email already registered"
            )

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        connection = get_db()

        user = connection.execute(
            """
            SELECT *
            FROM users
            WHERE email = ? AND password = ?
            """,
            (email, password)
        ).fetchone()

        connection.close()

        if user:
            session["user_id"] = user["id"]
            session["name"] = user["name"]
            session["role"] = user["role"]

            if user["role"] == "student":
                return redirect(url_for("student_dashboard"))

            return redirect(url_for("owner_dashboard"))

        return render_template(
            "login.html",
            error="Invalid email or password"
        )

    return render_template("login.html")


@app.route("/student/dashboard")
def student_dashboard():
    if "user_id" not in session or session["role"] != "student":
        return redirect(url_for("login"))

    connection = get_db()

    today = date.today().isoformat()

    student = connection.execute(
        """
        SELECT student_id
        FROM users
        WHERE id = ?
        """,
        (session["user_id"],)
    ).fetchone()

    meals = connection.execute(
        """
        SELECT *
        FROM meals
        WHERE meal_date = ?
        ORDER BY id
        """,
        (today,)
    ).fetchall()

    responses = connection.execute(
        """
        SELECT meal_id, response
        FROM meal_responses
        WHERE user_id = ?
        """,
        (session["user_id"],)
    ).fetchall()

    attendance = connection.execute(
        """
        SELECT meal_id, status
        FROM attendance
        WHERE user_id = ?
        """,
        (session["user_id"],)
    ).fetchall()

    attendance_history = connection.execute(
        """
        SELECT
            attendance.attendance_date,
            attendance.status,
            attendance.scan_time,
            meals.meal_type,
            meals.menu
        FROM attendance
        JOIN meals
            ON attendance.meal_id = meals.id
        WHERE attendance.user_id = ?
        ORDER BY attendance.attendance_date DESC, attendance.id DESC
        """,
        (session["user_id"],)
    ).fetchall()

    response_map = {
        row["meal_id"]: row["response"]
        for row in responses
    }

    attendance_map = {
        row["meal_id"]: row["status"]
        for row in attendance
    }

    connection.close()

    return render_template(
        "student_dashboard.html",
        name=session["name"],
        student_id=student["student_id"],
        meals=meals,
        response_map=response_map,
        attendance_map=attendance_map,
        attendance_history=attendance_history,
        today=today
    )


@app.route("/student/meal-response/<int:meal_id>", methods=["POST"])
def meal_response(meal_id):
    if "user_id" not in session or session["role"] != "student":
        return redirect(url_for("login"))

    response = request.form["response"]

    connection = get_db()

    meal = connection.execute(
        "SELECT * FROM meals WHERE id = ?",
        (meal_id,)
    ).fetchone()

    if not meal:
        connection.close()
        return redirect(url_for("student_dashboard"))

    current_time = datetime.now().strftime("%H:%M")

    if current_time > meal["deadline"]:
        connection.close()
        return redirect(url_for("student_dashboard"))

    existing = connection.execute(
        """
        SELECT id
        FROM meal_responses
        WHERE user_id = ? AND meal_id = ?
        """,
        (session["user_id"], meal_id)
    ).fetchone()

    if existing:
        connection.execute(
            """
            UPDATE meal_responses
            SET response = ?, created_at = ?
            WHERE id = ?
            """,
            (
                response,
                datetime.now().isoformat(),
                existing["id"]
            )
        )
    else:
        connection.execute(
            """
            INSERT INTO meal_responses
            (user_id, meal_id, response, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                session["user_id"],
                meal_id,
                response,
                datetime.now().isoformat()
            )
        )

    connection.commit()
    connection.close()

    return redirect(url_for("student_dashboard"))


@app.route("/owner/dashboard")
def owner_dashboard():
    if "user_id" not in session or session["role"] != "owner":
        return redirect(url_for("login"))

    selected_date = request.args.get(
        "date",
        date.today().isoformat()
    )

    connection = get_db()

    meals = connection.execute(
        """
        SELECT *
        FROM meals
        WHERE meal_date = ?
        ORDER BY id
        """,
        (selected_date,)
    ).fetchall()

    meal_data = []

    for meal in meals:
        expected = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM meal_responses
            WHERE meal_id = ?
            AND response = 'Eating'
            """,
            (meal["id"],)
        ).fetchone()["count"]

        actual = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM attendance
            WHERE meal_id = ?
            AND status = 'Present'
            """,
            (meal["id"],)
        ).fetchone()["count"]

        difference = expected - actual

        students = connection.execute(
            """
            SELECT
                users.name,
                users.email,
                users.student_id,
                attendance.status,
                attendance.scan_time
            FROM attendance
            JOIN users
                ON attendance.user_id = users.id
            WHERE attendance.meal_id = ?
            ORDER BY users.name
            """,
            (meal["id"],)
        ).fetchall()

        meal_data.append({
            "meal": meal,
            "expected": expected,
            "actual": actual,
            "difference": difference,
            "students": students
        })

    monthly_records = connection.execute(
        """
        SELECT
            users.name,
            users.email,
            users.student_id,
            attendance.attendance_date,
            meals.meal_type,
            meals.menu,
            attendance.status,
            attendance.scan_time
        FROM attendance
        JOIN users
            ON attendance.user_id = users.id
        JOIN meals
            ON attendance.meal_id = meals.id
        WHERE strftime('%Y-%m', attendance.attendance_date) = ?
        ORDER BY attendance.attendance_date DESC, users.name
        """,
        (selected_date[:7],)
    ).fetchall()

    connection.close()

    return render_template(
        "owner_dashboard.html",
        name=session["name"],
        meals=meal_data,
        today=date.today().isoformat(),
        selected_date=selected_date,
        monthly_records=monthly_records
    )


@app.route("/owner/student-report")
def student_report():
    if "user_id" not in session or session["role"] != "owner":
        return redirect(url_for("login"))

    student_id = request.args.get(
        "student_id",
        ""
    ).strip().upper()

    selected_month = request.args.get(
        "month",
        date.today().strftime("%Y-%m")
    )

    student = None
    records = []

    breakfast = 0
    lunch = 0
    dinner = 0

    connection = get_db()

    if student_id:
        student = connection.execute(
            """
            SELECT
                id,
                name,
                email,
                student_id
            FROM users
            WHERE student_id = ?
            AND role = 'student'
            """,
            (student_id,)
        ).fetchone()

        if student:
            records = connection.execute(
                """
                SELECT
                    attendance.attendance_date,
                    attendance.status,
                    attendance.scan_time,
                    meals.meal_type,
                    meals.menu
                FROM attendance
                JOIN meals
                    ON attendance.meal_id = meals.id
                WHERE attendance.user_id = ?
                AND strftime('%Y-%m', attendance.attendance_date) = ?
                ORDER BY
                    attendance.attendance_date,
                    meals.id
                """,
                (
                    student["id"],
                    selected_month
                )
            ).fetchall()

            for record in records:
                if record["status"] == "Present":
                    if record["meal_type"] == "Breakfast":
                        breakfast += 1
                    elif record["meal_type"] == "Lunch":
                        lunch += 1
                    elif record["meal_type"] == "Dinner":
                        dinner += 1

    connection.close()

    total = breakfast + lunch + dinner

    return render_template(
        "student_report.html",
        student=student,
        records=records,
        student_id=student_id,
        selected_month=selected_month,
        breakfast=breakfast,
        lunch=lunch,
        dinner=dinner,
        total=total
    )


@app.route("/owner/add-meal", methods=["POST"])
def add_meal():
    if "user_id" not in session or session["role"] != "owner":
        return redirect(url_for("login"))

    meal_type = request.form["meal_type"]
    meal_date = request.form["meal_date"]
    menu = request.form["menu"]
    start_time = request.form["start_time"]
    end_time = request.form["end_time"]
    deadline = request.form["deadline"]

    connection = get_db()

    connection.execute(
        """
        INSERT INTO meals
        (meal_type, meal_date, menu, start_time, end_time, deadline)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            meal_type,
            meal_date,
            menu,
            start_time,
            end_time,
            deadline
        )
    )

    connection.commit()
    connection.close()

    return redirect(
        url_for(
            "owner_dashboard",
            date=meal_date
        )
    )


@app.route("/owner/delete-meal/<int:meal_id>", methods=["POST"])
def delete_meal(meal_id):
    if "user_id" not in session or session["role"] != "owner":
        return redirect(url_for("login"))

    connection = get_db()

    meal = connection.execute(
        "SELECT meal_date FROM meals WHERE id = ?",
        (meal_id,)
    ).fetchone()

    connection.execute(
        "DELETE FROM meal_responses WHERE meal_id = ?",
        (meal_id,)
    )

    connection.execute(
        "DELETE FROM attendance WHERE meal_id = ?",
        (meal_id,)
    )

    connection.execute(
        "DELETE FROM meals WHERE id = ?",
        (meal_id,)
    )

    connection.commit()
    connection.close()

    if meal:
        return redirect(
            url_for(
                "owner_dashboard",
                date=meal["meal_date"]
            )
        )

    return redirect(url_for("owner_dashboard"))


@app.route("/owner/generate-qr/<int:meal_id>")
def generate_qr(meal_id):
    if "user_id" not in session or session["role"] != "owner":
        return redirect(url_for("login"))

    connection = get_db()

    meal = connection.execute(
        "SELECT * FROM meals WHERE id = ?",
        (meal_id,)
    ).fetchone()

    connection.close()

    if not meal:
        return redirect(url_for("owner_dashboard"))

    token = str(uuid.uuid4())

    connection = get_db()

    connection.execute(
        """
        INSERT OR REPLACE INTO settings
        (setting_name, setting_value)
        VALUES (?, ?)
        """,
        (
            f"qr_{meal_id}",
            token
        )
    )

    connection.commit()
    connection.close()

    qr_url = url_for(
        "scan_attendance",
        meal_id=meal_id,
        token=token,
        _external=True
    )

    qr = qrcode.make(qr_url)

    os.makedirs(
        "static/qrcodes",
        exist_ok=True
    )

    qr_path = f"static/qrcodes/meal_{meal_id}.png"

    qr.save(qr_path)

    return render_template(
        "qr_code.html",
        meal=meal,
        qr_path=qr_path,
        qr_url=qr_url
    )


@app.route("/scan/<int:meal_id>/<token>")
def scan_attendance(meal_id, token):
    if "user_id" not in session or session["role"] != "student":
        return redirect(url_for("login"))

    connection = get_db()

    saved_token = connection.execute(
        """
        SELECT setting_value
        FROM settings
        WHERE setting_name = ?
        """,
        (f"qr_{meal_id}",)
    ).fetchone()

    meal = connection.execute(
        "SELECT * FROM meals WHERE id = ?",
        (meal_id,)
    ).fetchone()

    if not saved_token or not meal or saved_token["setting_value"] != token:
        connection.close()

        return render_template(
            "attendance_result.html",
            success=False,
            message="Invalid QR code"
        )

    today = date.today().isoformat()

    if meal["meal_date"] != today:
        connection.close()

        return render_template(
            "attendance_result.html",
            success=False,
            message="This QR code is not valid today"
        )

    current_time = datetime.now().strftime("%H:%M")

    if current_time < meal["start_time"] or current_time > meal["end_time"]:
        connection.close()

        return render_template(
            "attendance_result.html",
            success=False,
            message="Meal attendance is not available at this time"
        )

    existing = connection.execute(
        """
        SELECT id
        FROM attendance
        WHERE user_id = ?
        AND meal_id = ?
        """,
        (
            session["user_id"],
            meal_id
        )
    ).fetchone()

    if existing:
        connection.close()

        return render_template(
            "attendance_result.html",
            success=False,
            message="Attendance already recorded"
        )

    connection.execute(
        """
        INSERT INTO attendance
        (user_id, meal_id, attendance_date, status, scan_time)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            session["user_id"],
            meal_id,
            today,
            "Present",
            datetime.now().strftime("%H:%M:%S")
        )
    )

    connection.commit()
    connection.close()

    return render_template(
        "attendance_result.html",
        success=True,
        message="Meal attendance recorded successfully"
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


init_db()

if __name__ == "__main__":
    app.run(debug=True)