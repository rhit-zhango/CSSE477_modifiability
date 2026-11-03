# CSSE477_modifiability
Here are the Python Server Build commands for the Lab:

Run these commands from the repository root, in order:
```sh
cd pyftpdlib
docker build -t exam-ftp-server .
docker run --rm -p 2121:2121 -p 60000-60010:60000-60010 exam-ftp-server
```

Leave the server running, then connect JFTP to `127.0.0.1:2121` with username `student` and password `password`. Stop the server with Ctrl+C. Feel free to change any ports for local testing; if you have two machines, you can also test without Docker. Run Docker commands where Docker is configured.

Example FTP manifest header (64 bytes):
`# EXAM_ID: <unique_id> | ENQ: NONE` padded with spaces to 63 bytes, followed by a newline. Use a unique ID for each upload you want to go through.