import requests
import base64
import json
import os
from dotenv import load_dotenv
load_dotenv()

EMAIL = os.getenv("JIRA_EMAIL")
API_TOKEN = os.getenv("JIRA_API_KEY")
DOMAIN = os.getenv("JIRA_DOMAIN")
PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY")

auth_str = f"{EMAIL}:{API_TOKEN}"
b64_auth = base64.b64encode(auth_str.encode()).decode()

headers = {
    "Authorization": f"Basic {b64_auth}",
    "Accept": "application/json",
    "Content-Type": "application/json"
}

url = f"https://{DOMAIN}/rest/api/3/issue"

payload = {
  "fields": {
    "project": {
      "key": PROJECT_KEY
    },
    "summary": "Fix login bug",
    "description": {
      "type": "doc",
      "version": 1,
      "content": [
        {
          "type": "paragraph",
          "content": [
            {
              "type": "text",
              "text": "Login page throws 500 error when user enters wrong password"
            }
          ]
        }
      ]
    },
    "issuetype": {
      "name": "Task"
    }
  }
}

response = requests.post(url, headers=headers, data=json.dumps(payload))