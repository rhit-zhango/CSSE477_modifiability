# CSSE477_modifiability
Here are the Python Server Build commands for the Lab:
docker build -t exam-ftp-server .
docker run --rm -p 2121:2121 -p 60000-60010:60000-60010 exam-ftp-server

Feel free to change any ports for local testing, if you have two machines you can also test without the docker, the docker is there to support people with only one machine.