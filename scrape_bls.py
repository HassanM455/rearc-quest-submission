import hashlib
import json
import os
import time

from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from google.cloud import storage


BASE_URL = "https://download.bls.gov/pub/time.series/pr/"
USER_AGENT = "scrape_bls.py  (mahmhz04@gmail.com)"

BUCKET_NAME = "rearc-quest-hassan-mahmood"
MANIFEST_NAME = "manifest.json"
PUBLIC_BUCKET_NAME = "rearc-quest-public-hassan-mahmood"

class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return

        for name, value in attrs:
            if name.lower() == "href" and value:
                self.hrefs.append(value)


def get_file_names(url):
    request = Request(
        url,
        headers={"User-Agent": USER_AGENT},
    )

    with urlopen(request, timeout=30) as response:
        html = response.read().decode("utf-8")

    parser = LinkParser()
    parser.feed(html)

    file_names = []

    for href in parser.hrefs:
        file_name = urlparse(
            urljoin(url, href)
        ).path.rsplit("/", 1)[-1]

        if not file_name:
            continue

        if href.startswith("../"):
            continue

        if href.endswith("/"):
            continue

        file_names.append(file_name)

    return file_names


def get_last_modified_timestamp(file_url):
    request = Request(
        file_url,
        headers={"User-Agent": USER_AGENT},
        method="HEAD",
    )

    with urlopen(request, timeout=30) as response:
        last_modified = response.headers.get("Last-Modified")

    if last_modified is None:
        return None

    return int(
        parsedate_to_datetime(last_modified).timestamp()
    )


def download_file(file_url, local_file_name):
    request = Request(
        file_url,
        headers={"User-Agent": USER_AGENT},
    )

    with urlopen(request, timeout=120) as response:
        with open(local_file_name, "wb") as output_file:
            while True:
                chunk = response.read(1024 * 1024)

                if not chunk:
                    break

                output_file.write(chunk)


def calculate_checksum(local_file_name):
    checksum = hashlib.sha256()

    with open(local_file_name, "rb") as input_file:
        while True:
            chunk = input_file.read(1024 * 1024)

            if not chunk:
                break

            checksum.update(chunk)

    return checksum.hexdigest()


def download_bls_files():
    file_names = get_file_names(BASE_URL)
    manifest_files = {}

    print("Found {} files on BLS.".format(len(file_names)))

    for file_name in file_names:
        file_url = urljoin(BASE_URL, file_name)
        timestamp = get_last_modified_timestamp(file_url)

        if timestamp is None:
            timestamp = int(time.time())

        local_file_name = "{}_{}".format(
            file_name,
            timestamp,
        )

        print(
            "Downloading {} as {}...".format(
                file_name,
                local_file_name,
            )
        )

        download_file(file_url, local_file_name)

        checksum = calculate_checksum(local_file_name)

        manifest_files[file_name] = {
            "original_name": file_name,
            "object_name": local_file_name,
            "last_modified": timestamp,
            "sha256": checksum,
        }

    return {
        "source_url": BASE_URL,
        "generated_at": int(time.time()),
        "files": manifest_files,
    }


def get_old_manifest(bucket):
    manifest_blob = bucket.blob(MANIFEST_NAME)

    if not manifest_blob.exists():
        return None

    manifest_text = manifest_blob.download_as_text()

    return json.loads(manifest_text)


def compare_manifests(old_manifest, new_manifest):
    old_files = old_manifest.get("files", {})
    new_files = new_manifest.get("files", {})

    old_names = set(old_files)
    new_names = set(new_files)

    files_to_add = new_names - old_names
    files_to_delete = old_names - new_names

    files_to_replace = set()

    for file_name in old_names & new_names:
        old_file = old_files[file_name]
        new_file = new_files[file_name]

        timestamp_changed = (
            old_file.get("last_modified")
            != new_file.get("last_modified")
        )

        checksum_changed = (
            old_file.get("sha256")
            != new_file.get("sha256")
        )

        if timestamp_changed or checksum_changed:
            files_to_replace.add(file_name)

    return (
        files_to_add,
        files_to_delete,
        files_to_replace,
    )

def upload_file(bucket, file_details):
    local_file_name = file_details["object_name"]
    object_name = file_details["object_name"]

    print("Uploading {}...".format(object_name))

    blob = bucket.blob(object_name)

    blob.metadata = {
        "original_name": file_details["original_name"],
        "last_modified": str(file_details["last_modified"]),
        "sha256": file_details["sha256"],
    }

    blob.upload_from_filename(local_file_name)


def delete_object(bucket, object_name):
    print("Deleting gs://{}/{}...".format(
        BUCKET_NAME,
        object_name,
    ))

    blob = bucket.blob(object_name)

    if blob.exists():
        blob.delete()


def upload_manifest(bucket, manifest):
    manifest_blob = bucket.blob(MANIFEST_NAME)

    manifest_blob.upload_from_string(
        json.dumps(manifest, indent=2),
        content_type="application/json",
    )

    print("Uploaded {}.".format(MANIFEST_NAME))


def synchronize_bucket():
    storage_client = storage.Client()

    private_bucket = storage_client.bucket(
        BUCKET_NAME
    )

    public_bucket = storage_client.bucket(
        PUBLIC_BUCKET_NAME
    )

    new_manifest = download_bls_files()
    old_manifest = get_old_manifest(private_bucket)

    if old_manifest is None:
        print("No existing manifest was found.")

        for file_details in new_manifest["files"].values():
            upload_file(
                private_bucket,
                file_details,
            )

    else:
        (
            files_to_add,
            files_to_delete,
            files_to_replace,
        ) = compare_manifests(
            old_manifest,
            new_manifest,
        )

        print(
            "Files to add: {}".format(
                sorted(files_to_add)
            )
        )
        print(
            "Files to delete: {}".format(
                sorted(files_to_delete)
            )
        )
        print(
            "Files to replace: {}".format(
                sorted(files_to_replace)
            )
        )

        for file_name in sorted(
            files_to_add | files_to_replace
        ):
            upload_file(
                private_bucket,
                new_manifest["files"][file_name],
            )

        for file_name in sorted(files_to_delete):
            old_object_name = (
                old_manifest["files"][file_name][
                    "object_name"
                ]
            )

            delete_object(
                private_bucket,
                old_object_name,
            )

        for file_name in sorted(files_to_replace):
            old_object_name = (
                old_manifest["files"][file_name][
                    "object_name"
                ]
            )

            new_object_name = (
                new_manifest["files"][file_name][
                    "object_name"
                ]
            )

            if old_object_name != new_object_name:
                delete_object(
                    private_bucket,
                    old_object_name,
                )

    # Update the private manifest only after the private objects
    # have been synchronized.
    upload_manifest(
        private_bucket,
        new_manifest,
    )

    # Synchronize data files from private to public.
    # manifest.json is explicitly excluded.
    synchronize_public_bucket(
        storage_client,
        private_bucket,
        public_bucket,
        new_manifest,
    )

    create_and_upload_index(
        public_bucket,
        new_manifest,
    )

    print("Private and public synchronization completed.")
    return new_manifest 


def synchronize_public_bucket(
    storage_client,
    private_bucket,
    public_bucket,
    manifest,
):
    desired_files = manifest["files"]

    # The public object names are the original BLS filenames.
    desired_public_names = set(desired_files.keys())

    public_blobs = {
        blob.name: blob
        for blob in storage_client.list_blobs(public_bucket)
    }

    # manifest.json should never exist in the public bucket.
    if MANIFEST_NAME in public_blobs:
        print(
            "Deleting gs://{}/{}...".format(
                PUBLIC_BUCKET_NAME,
                MANIFEST_NAME,
            )
        )
        public_blobs[MANIFEST_NAME].delete()
        del public_blobs[MANIFEST_NAME]

    current_public_names = set(public_blobs.keys())

    # Remove public objects that no longer exist in the BLS source.
    for object_name in sorted(
        current_public_names - desired_public_names
    ):
        print(
            "Deleting public object gs://{}/{}...".format(
                PUBLIC_BUCKET_NAME,
                object_name,
            )
        )

        public_blobs[object_name].delete()

    for file_name, file_details in desired_files.items():
        private_object_name = file_details["object_name"]
        expected_checksum = file_details["sha256"]
        expected_timestamp = str(
            file_details["last_modified"]
        )

        public_blob = public_blobs.get(file_name)

        must_copy = False

        if public_blob is None:
            print(
                "{} is missing from the public bucket.".format(
                    file_name
                )
            )
            must_copy = True
        else:
            metadata = public_blob.metadata or {}

            checksum_changed = (
                metadata.get("sha256")
                != expected_checksum
            )

            timestamp_changed = (
                metadata.get("last_modified")
                != expected_timestamp
            )

            if checksum_changed or timestamp_changed:
                print(
                    "{} has changed.".format(file_name)
                )
                must_copy = True

        if not must_copy:
            print(
                "{} is already current in the public bucket.".format(
                    file_name
                )
            )
            continue

        source_blob = private_bucket.blob(
            private_object_name
        )

        if not source_blob.exists():
            raise RuntimeError(
                "Private object does not exist: gs://{}/{}".format(
                    BUCKET_NAME,
                    private_object_name,
                )
            )

        print(
            "Copying gs://{}/{} to gs://{}/{}...".format(
                BUCKET_NAME,
                private_object_name,
                PUBLIC_BUCKET_NAME,
                file_name,
            )
        )

        copied_blob = private_bucket.copy_blob(
            source_blob,
            public_bucket,
            new_name=file_name,
        )

        # Explicitly set the metadata used during future comparisons.
        copied_blob.metadata = {
            "original_name": file_name,
            "last_modified": expected_timestamp,
            "sha256": expected_checksum,
        }
        copied_blob.patch()

def clean_up_local_files(manifest):
    for file_details in manifest["files"].values():
        local_file_name = file_details["object_name"]

        if os.path.exists(local_file_name):
            os.remove(local_file_name)
            print("Deleted local file: {}".format(local_file_name))

def create_and_upload_index(public_bucket, manifest):
    file_names = sorted(manifest["files"].keys())

    file_links = []

    for file_name in file_names:
        file_links.append(
            '        <li><a href="./{0}" download>{0}</a></li>'.format(
                file_name
            )
        )

    html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">

  <title>BLS Productivity Data Files</title>

  <style>
    body {{
      margin: 0;
      font-family: Arial, sans-serif;
      background: #f4f6f8;
      color: #1f2933;
    }}

    main {{
      max-width: 800px;
      margin: 60px auto;
      padding: 0 20px;
    }}

    .card {{
      background: white;
      border: 1px solid #d9e2ec;
      border-radius: 10px;
      padding: 32px;
      box-shadow: 0 4px 14px rgba(0, 0, 0, 0.06);
    }}

    h1 {{
      margin-top: 0;
      font-size: 28px;
    }}

    p {{
      color: #52606d;
      line-height: 1.5;
    }}

    ul {{
      list-style: none;
      padding: 0;
      margin-top: 28px;
    }}

    li {{
      border-top: 1px solid #e4e7eb;
    }}

    li:last-child {{
      border-bottom: 1px solid #e4e7eb;
    }}

    a {{
      display: block;
      padding: 14px 10px;
      color: #1261a0;
      text-decoration: none;
      font-family: monospace;
      font-size: 15px;
    }}

    a:hover {{
      background: #f0f4f8;
      text-decoration: underline;
    }}
  </style>
</head>

<body>
  <main>
    <section class="card">
      <h1>BLS Productivity Data Files</h1>

      <p>Select a file below to view or download it.</p>

      <ul>
{file_links}
      </ul>
    </section>
  </main>
</body>
</html>
""".format(
        file_links="\n".join(file_links)
    )

    local_file_name = "index.html"

    with open(local_file_name, "w") as index_file:
        index_file.write(html)

    blob = public_bucket.blob(local_file_name)

    blob.upload_from_filename(
        local_file_name,
        content_type="text/html",
    )

    print(
        "Uploaded gs://{}/{}".format(
            PUBLIC_BUCKET_NAME,
            local_file_name,
        )
    )


if __name__ == "__main__":
    manifest = synchronize_bucket()
    clean_up_local_files(manifest)
