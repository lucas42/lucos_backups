import json, requests

SCHEDULE_TRACKER_ENDPOINT = os.environ.get('SCHEDULE_TRACKER_ENDPOINT')
if not SCHEDULE_TRACKER_ENDPOINT:
	exit("SCHEDULE_TRACKER_ENDPOINT environment variable not set - needs to be the URL of a running lucos_contacts instance.")

# Inform the schedule tracker that the job is complete
def updateScheduleTracker(system="lucos_backups", success=True, message=None, frequency=(24 * 60 * 60)):
	payload = {
		"system": system,
		"frequency": frequency,
		"status": "success" if success else "error",
		"message": message,
	}
	schedule_tracker_response = requests.post(SCHEDULE_TRACKER_ENDPOINT, json=payload);
	if not schedule_tracker_response.ok:
		print ("\033[91m** Error ** Call to schedule-tracker failed with "+str(schedule_tracker_response.status_code)+" response: " +  schedule_tracker_response.text + "\033[0m", flush=True)