import json, requests

# Inform the schedule tracker that the job is complete
def updateScheduleTracker(success=True, message=None):
	payload = {
		"system": "lucos_backups",
		"frequency": 24 * 60 * 60, # 1 day, in seconds
		"status": "success" if success else "error",
		"message": message,
	}
	schedule_tracker_response = requests.post('https://schedule-tracker.l42.eu/report-status', json=payload);
	if not schedule_tracker_response.ok:
		print ("\033[91m** Error ** Call to schedule-tracker failed with "+str(schedule_tracker_response.status_code)+" response: " +  schedule_tracker_response.text + "\033[0m")