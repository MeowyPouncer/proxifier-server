import json
import time
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler
from bs4 import BeautifulSoup
from flask import Flask, Response
from flask_httpauth import HTTPBasicAuth
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

base_dir = Path(__file__).resolve().parent
log_file_path = base_dir / 'logs/router.log'
data_file_path = base_dir / 'proxied_data.json'
bad_servers_path = base_dir / 'bad_servers.json'

auth = HTTPBasicAuth()
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{base_dir / "users.db"}'
db = SQLAlchemy(app)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
handler = RotatingFileHandler(log_file_path, maxBytes=50000, backupCount=1, encoding='utf-8')
handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'))
logger.addHandler(handler)
app.logger.addHandler(handler)

URL_MAP = {
    "placeholder": "https://example.com"
}

DATA_VERIFIER = {
    "string to be checked"
}

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
        logger.debug(f"Set password for user {self.username}")

    def check_password(self, password):
        result = check_password_hash(self.password_hash, password)
        logger.debug(f"Password check for user {self.username}: {'successful' if result else 'failed'}")
        return result


@auth.verify_password
def verify_password(username, password):
    logger.debug("Attempting to verify password")
    user = User.query.filter_by(username=username).first()
    if not username or not password:
        logger.info('Authentication failed - username or password missing')
        return False
    if not user:
        logger.info(f"Authentication failed - user {username} does not exist")
        return False
    elif not user.check_password(password):
        logger.info(f'Authentication failed - incorrect password for user {username}')
        return False
    logger.info(f'Authentication successful for user {username}')
    return True


@app.route('/get_content/<content_type>', methods=['GET'])
# @auth.login_required
def get_content(content_type):
    target_url = URL_MAP.get(content_type)
    if not target_url:
        return Response("Content type not supported", status=400)
    content = fetch_content_through_proxy(target_url)
    return Response(content, mimetype='application/javascript')


def initialize_webdriver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-gpu")
    return webdriver.Chrome(options=options)


def load_bad_servers():
    try:
        if bad_servers_path.exists():
            with open(bad_servers_path, 'r') as file:
                bad_servers = json.load(file)
        else:
            bad_servers = {}
    except FileNotFoundError:
        bad_servers = {}
    return bad_servers


def save_bad_servers(bad_servers):
    with open(bad_servers_path, 'w') as file:
        json.dump(bad_servers, file)


def fetch_content_through_proxy(target_url):
    driver = initialize_webdriver()
    logger.debug("Webdriver initialized.")
    bad_servers = load_bad_servers()
    logger.debug(f"Loaded bad servers: {bad_servers}")
    content = ''

    try:
        for attempt in range(5):
            logger.debug(f"Attempt {attempt+1} of 5")
            driver.get("https://proxyium.com")
            logger.info("Navigated to https://proxyium.com")
            try:
                consent_button = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, "//p[contains(text(), 'Consent')]"))
                )
                consent_button.click()
                logger.debug("Consent button clicked.")
            except Exception as e:
                logger.error(f"Consent button not found or not clickable: {str(e)}")

            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "web_proxy_form")))
            logger.debug("Web proxy form is present.")

            dropdown_menu = driver.find_element(By.ID, "unique-nice-select")
            dropdown_menu.click()
            logger.debug("Dropdown menu opened.")
            server_elements = driver.find_elements(By.CSS_SELECTOR, "#unique-nice-select .list .option")
            logger.info(f"Server elements found: {len(server_elements)}")

            for elem in server_elements:
                server_code = elem.get_attribute('data-value')
                logger.debug(f"Processing server: {server_code}")
                if server_code in bad_servers:
                    logger.info(f"Skipping bad server: {server_code}")
                    continue

                elem.click()
                logger.debug(f"Clicked on server {server_code}.")

                url_input = driver.find_element(By.ID, "unique-form-control")
                url_input.clear()
                url_input.send_keys(target_url)
                logger.debug(f"URL {target_url} entered into the input field.")

                driver.find_element(By.ID, "unique-btn-blue").click()
                logger.debug("Submit button clicked.")

                time.sleep(10)
                page_source = driver.page_source
                soup = BeautifulSoup(page_source, 'html.parser')
                current_content = soup.get_text()
                logger.debug("Page loaded, content extracted.")

                if DATA_VERIFIER in current_content or DATA_VERIFIER in current_content:
                    content = current_content
                    logger.info("Desired content found.")
                    break
                else:
                    bad_servers[server_code] = 1
                    logger.info(f"Server {server_code} added to bad servers list.")

            if content:
                break

        if not content:
            save_bad_servers(bad_servers)
            logger.info("No content found after all attempts, bad servers saved.")

    finally:
        driver.quit()
        logger.debug("Webdriver quit.")

    return content

if __name__ == '__main__':
    app.run()