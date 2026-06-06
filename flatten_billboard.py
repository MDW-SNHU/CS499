import json

INPUT_FILE = "all.json"
OUTPUT_FILE = "billboard.json"

def flatten_billboard(input_path, output_path):
    with open(input_path, "r", encoding="utf-8") as infile:
        data = json.load(infile)

    with open(output_path, "w", encoding="utf-8") as outfile:
        for entry in data:
            date = entry.get("date")
            songs = entry.get("data", [])

            for song in songs:
                # Inject the date into each song record
                flat_record = {
                    "date": date,
                    **song
                }
                outfile.write(json.dumps(flat_record) + "\n")

    print(f"Flattened data written to {output_path}")

if __name__ == "__main__":
    flatten_billboard(INPUT_FILE, OUTPUT_FILE)
