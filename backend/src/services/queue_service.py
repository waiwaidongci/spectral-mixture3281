from redis import Redis
from ..config.settings import REDIS_URL
queue=Redis.from_url(REDIS_URL,decode_responses=True)
RUN_JOBS="spectral:jobs"
CALIBRATION_JOBS="spectral:calibration"
def enqueue_run(run_id):return queue.rpush(RUN_JOBS,str(run_id))
def enqueue_item(item_id):return queue.rpush(CALIBRATION_JOBS,"cal:"+str(item_id))
