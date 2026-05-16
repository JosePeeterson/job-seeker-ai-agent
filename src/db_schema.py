import sqlite3
from pathlib import Path

def init_db(db_path: str):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    # Jobs table
    c.execute('''
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            company TEXT,
            url TEXT,
            description TEXT,
            score REAL,
            status TEXT,
            tailored_resume_path TEXT,
            cover_letter_path TEXT,
            application_date TEXT
        )
    ''')
    # Applications table
    c.execute('''
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER,
            applied_on TEXT,
            status TEXT,
            notes TEXT,
            FOREIGN KEY(job_id) REFERENCES jobs(id)
        )
    ''')
    # Resume versions table
    c.execute('''
        CREATE TABLE IF NOT EXISTS resume_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT,
            created_on TEXT,
            notes TEXT
        )
    ''')
    # Company contacts table
    c.execute('''
        CREATE TABLE IF NOT EXISTS company_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT,
            contact_name TEXT,
            email TEXT,
            phone TEXT,
            notes TEXT
        )
    ''')
    # Job preferences table
    c.execute('''
        CREATE TABLE IF NOT EXISTS job_preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            preference_order INTEGER,
            area TEXT
        )
    ''')
    conn.commit()
    conn.close()

if __name__ == "__main__":
    db_file = Path(__file__).parent.parent / "data" / "jobs.db"
    init_db(str(db_file))
