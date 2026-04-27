'''
Shell
Strategy classes for shell-flavour-specific command execution.

GnuShell:     standard GNU coreutils (find -printf, ls --time-style=long-iso, df -P).
              Used by every existing host.

BusyBoxShell: uses connection.sftp() to walk the tree and read st_size/st_mtime
              directly, avoiding BusyBox's date-format-quirky ls/stat output.
              Uses plain `df` parsed by column position for disk space.
'''
import stat
from datetime import datetime


def _format_bytes(size_bytes):
	'''Convert a byte count to a human-readable string (e.g. 1.2G, 340M)'''
	size_bytes = int(size_bytes)
	for unit in ['', 'K', 'M', 'G', 'T']:
		if abs(size_bytes) < 1024.0:
			return '{:.1f}{}'.format(size_bytes, unit)
		size_bytes /= 1024.0
	return '{:.1f}P'.format(size_bytes)


class GnuShell:
	def __init__(self, connection, backup_root):
		self.connection = connection
		self.backup_root = backup_root

	def ensure_one_off_dir(self, directory):
		self.connection.run('mkdir -p {directory}'.format(directory=directory), timeout=10)
		# Allow any user in the group to add files to the one-off directory
		self.connection.run('chmod g+w {directory}'.format(directory=directory), timeout=10)

	def list_one_off_files(self, directory):
		'''Returns list of dicts: {path, modification_date, size}'''
		raw = self.connection.run(
			'ls -l --human-readable --time-style=long-iso --literal {directory}'.format(directory=directory),
			hide=True, timeout=10,
		).stdout.splitlines()
		del raw[0]  # drop header line
		result = []
		for file_info in raw:
			cols = file_info.split(maxsplit=7)
			result.append({
				'path': directory + cols[7],
				'modification_date': cols[5],
				'size': cols[4],
			})
		return result

	def disk_space(self):
		'''Returns dict: {free_bytes, free_readable, used_percentage}'''
		free_bytes = int(self.connection.run(
			"df -P {backup_root} | tail -1 | awk '{{print $4}}'".format(backup_root=self.backup_root),
			hide=True, timeout=10,
		).stdout.strip())
		free_readable = self.connection.run(
			"df -Ph {backup_root} | tail -1 | awk '{{print $4}}'".format(backup_root=self.backup_root),
			hide=True, timeout=10,
		).stdout.strip()
		used_percentage = int(self.connection.run(
			"df -P {backup_root} | tail -1 | awk '{{print $5}}' | sed 's/%//'".format(backup_root=self.backup_root),
			hide=True, timeout=10,
		).stdout.strip())
		return {
			'free_bytes': free_bytes,
			'free_readable': free_readable,
			'used_percentage': used_percentage,
		}

	def list_backup_dir(self):
		'''Returns list of dicts: {name, date}'''
		raw = self.connection.run(
			'ls -l --time-style=long-iso --literal {backup_root}'.format(backup_root=self.backup_root),
			hide=True, timeout=10,
		).stdout.splitlines()
		del raw[0]  # drop header line
		files = []
		for file_info in raw:
			cols = file_info.split(maxsplit=7)
			files.append({'name': cols[7], 'date': cols[5]})
		return files

	def find_backup_files(self):
		'''Returns list of tab-separated strings: "YYYY-MM-DD\\tsize_bytes\\tfilepath"'''
		return self.connection.run(
			"find {ROOT_DIR} -wholename '{ROOT_DIR}*/**' -type f -printf \"%TY-%Tm-%Td\\t%s\\t%p\\n\"".format(ROOT_DIR=self.backup_root),
			hide=True, timeout=60,
		).stdout.splitlines()


class BusyBoxShell:
	def __init__(self, connection, backup_root):
		self.connection = connection
		self.backup_root = backup_root

	def _sftp(self):
		return self.connection.sftp()

	def ensure_one_off_dir(self, directory):
		sftp = self._sftp()
		# Create each path component via SFTP (mkdir is not recursive on SFTP)
		path = ''
		for part in directory.strip('/').split('/'):
			path = path + '/' + part
			try:
				sftp.mkdir(path)
			except OSError:
				pass  # already exists

	def list_one_off_files(self, directory):
		'''Returns list of dicts: {path, modification_date, size}'''
		sftp = self._sftp()
		try:
			attrs = sftp.listdir_attr(directory)
		except FileNotFoundError:
			return []
		result = []
		for attr in attrs:
			if attr.filename.startswith('.'):
				continue
			if stat.S_ISREG(attr.st_mode):
				mod_date = datetime.fromtimestamp(attr.st_mtime).strftime('%Y-%m-%d')
				result.append({
					'path': directory.rstrip('/') + '/' + attr.filename,
					'modification_date': mod_date,
					'size': _format_bytes(attr.st_size),
				})
		return result

	def disk_space(self):
		'''Returns dict: {free_bytes, free_readable, used_percentage}
		Uses plain `df` (no -P) and parses by column position.
		BusyBox df output: Filesystem 1K-blocks Used Available Use% Mounted'''
		output = self.connection.run(
			'df {backup_root}'.format(backup_root=self.backup_root),
			hide=True, timeout=10,
		).stdout.splitlines()
		cols = output[1].split()
		free_bytes = int(cols[3]) * 1024  # 1K-blocks → bytes
		used_percentage = int(cols[4].rstrip('%'))
		return {
			'free_bytes': free_bytes,
			'free_readable': _format_bytes(free_bytes),
			'used_percentage': used_percentage,
		}

	def list_backup_dir(self):
		'''Returns list of dicts: {name, date}'''
		sftp = self._sftp()
		try:
			attrs = sftp.listdir_attr(self.backup_root)
		except FileNotFoundError:
			return []
		files = []
		for attr in attrs:
			if attr.filename.startswith('.'):
				continue
			mod_date = datetime.fromtimestamp(attr.st_mtime).strftime('%Y-%m-%d')
			files.append({'name': attr.filename, 'date': mod_date})
		return files

	def find_backup_files(self):
		'''Walk the backup tree via SFTP; return tab-separated strings matching
		GnuShell.find_backup_files() format: "YYYY-MM-DD\\tsize_bytes\\tpath"'''
		sftp = self._sftp()
		results = []
		self._walk(sftp, self.backup_root, results)
		return results

	def _walk(self, sftp, current, results):
		try:
			attrs = sftp.listdir_attr(current)
		except Exception:
			return
		for attr in attrs:
			if attr.filename.startswith('.'):
				continue
			path = current.rstrip('/') + '/' + attr.filename
			if stat.S_ISREG(attr.st_mode):
				# Only include files at depth >= 2 below backup_root, matching
				# find's -wholename '{ROOT}*/**' pattern
				rel = path[len(self.backup_root.rstrip('/')):]
				if rel.count('/') >= 2:
					mod_date = datetime.fromtimestamp(attr.st_mtime).strftime('%Y-%m-%d')
					results.append('{}\t{}\t{}'.format(mod_date, attr.st_size, path))
			elif stat.S_ISDIR(attr.st_mode):
				self._walk(sftp, path, results)
