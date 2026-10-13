"""Redact credential-shaped text; traceback capture never includes local values."""
import os
import re


def redact_sensitive_text(text):
    for key in ('FUBON_PERSONAL_ID','FUBON_API_KEY','FUBON_CERT_PASSWORD','FUBON_CERT_PATH','GITHUB_TOKEN'):
        value=os.environ.get(key)
        if value:
            text=text.replace(value,'[REDACTED]')
    # Preserve frames, file paths, source expressions and ordinary type errors.
    text=re.sub(r'(?im)((?:FUBON_[A-Z_]+|api[_ -]?key|personal[_ -]?id|cert(?:ificate)?[_ -]?password|password|account(?:[_ -]?(?:number|no))?|authorization|credential|token)\s*[:=]\s*)[^\n]+',r'\1[REDACTED]',text)
    text=re.sub(r'\b[A-Z][12][0-9]{8}\b','[REDACTED ID]',text)
    text=re.sub(r'\b[0-9]{7,}\b','[REDACTED NUMBER]',text)
    text=re.sub(r'(?:ghp_|github_pat_)[A-Za-z0-9_]+','[REDACTED TOKEN]',text)
    return text
