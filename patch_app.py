import re

with open("src/broker/app.py", "r") as f:
    text = f.read()

# Fix import
if "import logging" not in text:
    text = text.replace("import json", "import json\nimport logging")
text = text.replace("                                import logging\n", "")

# Fix except Exception
text = re.sub(r'                        except Exception:\n                            req_id = ""', '                        except (json.JSONDecodeError, TypeError, KeyError, AttributeError):\n                            req_id = ""', text)

with open("src/broker/app.py", "w") as f:
    f.write(text)

