#!/bin/bash
echo "=== Weather Dashboard Logs ==="
docker compose -f /home/frigate/repos/weather-project/docker-compose.yml logs --tail 100 weather-dashboard

echo -e "\n=== Recent Errors ==="
docker compose -f /home/frigate/repos/weather-project/docker-compose.yml logs weather-dashboard | grep -i "error\|exception\|traceback\|keyerror" | tail -n 20