FROM python:3.13-slim

WORKDIR /code
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Download the tokenizer at build time, not on the first user request.
ENV TIKTOKEN_CACHE_DIR=/code/.tiktoken
RUN python -c "import tiktoken; tiktoken.get_encoding('o200k_base')"
COPY app ./app
COPY prompts ./prompts

# Port 80 matches the placeholder image Bicep deploys before azd pushes this one.
EXPOSE 80
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80"]
