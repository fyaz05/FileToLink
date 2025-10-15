pkill -f "python3 -m Thunder" || true
pkill -f "python3 update.py" || true
sleep 2
python3 update.py && python3 -m Thunder