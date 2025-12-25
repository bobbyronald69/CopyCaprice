import requests
import json
import os
from datetime import datetime
import pytz
import google.generativeai as genai

# ----------------------------------------------------------
# Load environment variables (GitHub Actions injects secrets)
# ----------------------------------------------------------
X_BEARER = os.getenv("X_BEARER_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
TARGET_USER_ID = os.getenv("TARGET_USER_ID")
PROCESSED_FILE = "processed_tweets.json"

genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")


# ----------------------------------------------------------
# Check if market is open (Weekdays 09:30–16:00 ET)
# ----------------------------------------------------------
def is_rth():
    eastern = pytz.timezone("US/Eastern")
    now = datetime.now(eastern)

    # Monday=0 ... Sunday=6
    if now.weekday() >= 5:
        print("Weekend detected. Exiting.")
        return False

    # RTH window
    open_time = now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_time = now.replace(hour=16, minute=0, second=0, microsecond=0)

    if open_time <= now <= close_time:
        return True
    
    print(f"Outside RTH. Current ET time: {now.strftime('%H:%M:%S')}")
    return False


# ----------------------------------------------------------
# Load/save processed tweets
# ----------------------------------------------------------
def load_processed():
    if not os.path.exists(PROCESSED_FILE):
        return set()
    with open(PROCESSED_FILE, "r") as f:
        return set(json.load(f))


def save_processed(processed):
    with open(PROCESSED_FILE, "w") as f:
        json.dump(list(processed), f)


# ----------------------------------------------------------
# Step 1: Fetch tweets
# ----------------------------------------------------------
def get_latest_tweets():
    url = f"https://api.twitter.com/2/users/{TARGET_USER_ID}/tweets"
    params = {
        "expansions": "attachments.media_keys",
        "media.fields": "type,url"
    }

    headers = {"Authorization": f"Bearer {X_BEARER}"}
    r = requests.get(url, headers=headers, params=params)

    if r.status_code != 200:
        print("Error fetching tweets:", r.text)
        return None

    return r.json()


# ----------------------------------------------------------
# Step 2: Skip tweets with images
# ----------------------------------------------------------
def has_images(tweet, includes):
    if "attachments" not in tweet:
        return False
    if "media_keys" not in tweet["attachments"]:
        return False
    if "media" not in includes:
        return False

    media_keys = tweet["attachments"]["media_keys"]
    for m in includes["media"]:
        if m["media_key"] in media_keys and m.get("type") == "photo":
            return True
    return False


# ----------------------------------------------------------
# Step 3: Classify (Gemini)
# ----------------------------------------------------------
def classify_trade(text):
    prompt = f"""
You are a classifier. Determine if this tweet describes an options
trade execution. Respond with exactly:

trade
or
not trade

Tweet:
{text}
"""

    try:
        result = model.generate_content(prompt)
        ans = result.text.strip().lower()
        return "trade" if ans.startswith("trade") else "not trade"
    except Exception as e:
        print("Gemini classification error:", e)
        return "not trade"


# ----------------------------------------------------------
# Step 4: Rewrite (Gemini)
# ----------------------------------------------------------
def rewrite_tweet(text):
    prompt = f"""
Rewrite this options execution tweet in an unprofessional, cute, format.

Rules:
- Preserve ticker, strike, expiry, and pricing details.
- Remove slang and emojis.
- Make the execution clear.

Original:
{text}
"""

    try:
        result = model.generate_content(prompt)
        return result.text.strip()
    except Exception as e:
        print("Gemini rewrite error:", e)
        return None


# ----------------------------------------------------------
# Step 5: Post rewritten tweet
# ----------------------------------------------------------
def post_tweet(text):
    print("Posting:", text)

    url = "https://api.twitter.com/2/tweets"
    headers = {
        "Authorization": f"Bearer {X_BEARER}",
        "Content-Type": "application/json"
    }
    data = {"text": text}

    r = requests.post(url, headers=headers, json=data)

    if r.status_code != 201:
        print("Error posting tweet:", r.text)
        return False

    return True


# ----------------------------------------------------------
# Main execution (runs only during RTH)
# ----------------------------------------------------------
def run():
    if not is_rth():
        return  # Exit quietly

    processed = load_processed()
    print("Loaded processed tweets:", len(processed))

    data = get_latest_tweets()
    if not data:
        return

    tweets = data.get("data", [])
    includes = data.get("includes", {})

    for t in tweets:
        tid = t["id"]
        text = t["text"]

        if tid in processed:
            continue

        # Skip image tweets
        if has_images(t, includes):
            print(f"Skipping {tid}: contains images.")
            processed.add(tid)
            continue

        # Classify
        if classify_trade(text) != "trade":
            print(f"Skipping {tid}: not a trade.")
            processed.add(tid)
            continue

        # Rewrite
        rewritten = rewrite_tweet(text)
        if not rewritten:
            print(f"Skipping {tid}: rewrite failed.")
            processed.add(tid)
            continue

        # Post
        if post_tweet(rewritten):
            print(f"Posted rewritten tweet for {tid}.")
        else:
            print(f"Failed to post {tid}.")

        processed.add(tid)

    save_processed(processed)
    print("Done.")


if __name__ == "__main__":
    run()
