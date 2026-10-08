import os

def fix_indexes():
    files = [
        ("/opt/seleric/mage-ai/infra/cube/model_v2/cubes/commerce.yml", "- order_date\n"),
        ("/opt/seleric/mage-ai/infra/cube/model_v2/cubes/finance.yml", "- report_date\n"),
        ("/opt/seleric/mage-ai/infra/cube/model_v2/cubes/paid_media.yml", "- report_date\n")
    ]
    
    for filepath, to_remove in files:
        with open(filepath, "r") as f:
            content = f.read()
            
        content = content.replace(f"      {to_remove}", "")
        
        with open(filepath, "w") as f:
            f.write(content)
            
    print("Fixed indexes by removing time dimensions.")

if __name__ == "__main__":
    fix_indexes()
