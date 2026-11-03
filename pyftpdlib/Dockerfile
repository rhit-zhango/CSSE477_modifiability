FROM python:3.11-slim

ENV PYTHONPATH=/opt/pyftpdlib \
    PYTHONDONTWRITEBYTECODE=1

COPY pyftpdlib /opt/pyftpdlib/pyftpdlib

WORKDIR /srv/ftp
RUN mkdir -p /srv/ftp/workspace

EXPOSE 2121 60000-60010

CMD ["python", "-m", "pyftpdlib", "--interface", "0.0.0.0", "--port", "2121", "--directory", "/srv/ftp/workspace", "--username", "student", "--password", "password", "--write", "--range", "60000-60010", "--banner", "Metadata validation FTP server ready."]
