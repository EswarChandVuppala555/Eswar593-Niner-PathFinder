import csv
import json
import os

# Paths
DATA_DIR = "data"
CSV_FILE = os.path.join(DATA_DIR, "feedback_log.csv")
OUTPUT_FILE = os.path.join(DATA_DIR, "feedback_dataset.jsonl")

def preprocess_feedback():
    if not os.path.exists(CSV_FILE):
        print(f"❌ No feedback file found at {CSV_FILE}")
        return

    with open(CSV_FILE, mode="r", encoding="utf-8") as f_in, \
         open(OUTPUT_FILE, mode="w", encoding="utf-8") as f_out:

        reader = csv.DictReader(f_in)
        count = 0

        for row in reader:
            # Conversation history (stored as raw string in CSV → already stringified)
            conversation_history = row.get("conversation_history", "")

            # Feedback signal
            feedback_type = row.get("feedback_type", "").lower().strip()
            feedback_text = row.get("feedback_text", "").strip()
            chat_response = row.get("chat_response", "").strip()

            # Map like/dislike → reinforcement
            if feedback_type in ["positive", "like", "👍"]:
                reinforcement = "This answer was correct and useful."
            elif feedback_type in ["negative", "dislike", "👎"]:
                reinforcement = "This answer was incorrect or not helpful."
            else:
                reinforcement = "No feedback given."

            # Construct OpenAI fine-tuning style JSONL object
            json_obj = {
                "messages": [
                    {"role": "system", "content": "You are an academic advising assistant."},
                    {"role": "user", "content": conversation_history},
                    {"role": "assistant", "content": chat_response},
                    {"role": "user", "content": f"Feedback: {reinforcement}. Notes: {feedback_text}"}
                ]
            }

            f_out.write(json.dumps(json_obj) + "\n")
            count += 1

        print(f"✅ Processed {count} feedback rows into {OUTPUT_FILE}")

if __name__ == "__main__":
    preprocess_feedback()
