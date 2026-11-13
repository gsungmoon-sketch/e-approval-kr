# 선택: 컨테이너 실행. 데이터는 볼륨 /data 에 저장.
#   docker build -t gyeoljae .
#   docker run --rm -it -v gyeoljae-data:/data gyeoljae setup          # 최초 관리자
#   docker run -d -p 127.0.0.1:8130:8130 -v gyeoljae-data:/data gyeoljae
FROM python:3.12-slim
RUN useradd -r -u 10001 gyeol && mkdir /data && chown gyeol /data
COPY src /app/src
ENV PYTHONPATH=/app/src GYEOLJAE_DATA=/data PYTHONUNBUFFERED=1
USER gyeol
VOLUME /data
EXPOSE 8130
ENTRYPOINT ["python", "-m", "gyeoljae"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8130"]
