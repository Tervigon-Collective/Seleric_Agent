import os
import glob

def fix_files():
    directory = "/opt/seleric/mage-ai/infra/cube"
    files = glob.glob(f"{directory}/**/*.yml", recursive=True)
    count = 0
    for file in files:
        with open(file, "r") as f:
            content = f.read()
        
        if "sql: orders\n" in content or "sql: orders\r" in content:
            new_content = content.replace("sql: orders\n", "sql: '{CUBE}.orders'\n")
            new_content = new_content.replace("sql: orders\r", "sql: '{CUBE}.orders'\r")
            with open(file, "w") as f:
                f.write(new_content)
            count += 1
            print(f"Fixed {file}")
            
    print(f"Fixed {count} files.")

if __name__ == "__main__":
    fix_files()
