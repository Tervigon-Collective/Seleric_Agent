import os

def add_indexes():
    # 1. commerce.yml
    commerce = "/opt/seleric/mage-ai/infra/cube/model_v2/cubes/commerce.yml"
    with open(commerce, "r") as f:
        content = f.read()
    if "indexes:\n" not in content:
        content = content.replace(
            "    refresh_key:\n      every: 1 hour\n",
            "    refresh_key:\n      every: 1 hour\n    indexes:\n    - name: main\n      columns:\n      - brand_id\n      - order_date\n"
        )
        with open(commerce, "w") as f:
            f.write(content)
            
    # 2. finance.yml
    finance = "/opt/seleric/mage-ai/infra/cube/model_v2/cubes/finance.yml"
    with open(finance, "r") as f:
        content = f.read()
    if "indexes:\n" not in content:
        content = content.replace(
            "    refresh_key:\n      every: 1 hour\n",
            "    refresh_key:\n      every: 1 hour\n    indexes:\n    - name: main\n      columns:\n      - brand_id\n      - report_date\n"
        )
        with open(finance, "w") as f:
            f.write(content)
            
    # 3. paid_media.yml
    paid_media = "/opt/seleric/mage-ai/infra/cube/model_v2/cubes/paid_media.yml"
    with open(paid_media, "r") as f:
        content = f.read()
    if "indexes:\n" not in content:
        content = content.replace(
            "    refresh_key:\n      every: 1 hour\n",
            "    refresh_key:\n      every: 1 hour\n    indexes:\n    - name: main\n      columns:\n      - brand_id\n      - report_date\n"
        )
        with open(paid_media, "w") as f:
            f.write(content)
            
    print("Added indexes successfully.")

if __name__ == "__main__":
    add_indexes()
