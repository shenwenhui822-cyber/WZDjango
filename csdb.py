from pymongo import MongoClient
from pymongo.errors import ConnectionFailure
print("start test_mongodb_connection")
# 连接配置
connection_config = {
    'user': 'admin',
    'pwd': 'admin_wonderwz',
    'host': '192.168.110.199',
    'port': 27017
}

def test_mongodb_connection():
    try:
        # 创建连接字符串（MongoDB需要认证时）
        uri = f"mongodb://{connection_config['user']}:{connection_config['pwd']}@" \
              f"{connection_config['host']}:{connection_config['port']}/"
        
        # 创建MongoClient对象
        client = MongoClient(uri)
        
        # 测试连接（ping方法会验证连接是否成功）
        client.admin.command('ping')
        print("[OK] MongoDB连接成功！")
        
        # 可选：列出所有数据库（测试权限）
        print("\n可用数据库列表:")
        for db_name in client.list_database_names():
            print(f"- {db_name}")
            
    except ConnectionFailure as e:
        print(f"[FAIL] MongoDB连接失败: {str(e)}")
    except Exception as e:
        print(f"[FAIL] 发生错误: {str(e)}")
    finally:
        # 关闭连接（虽然MongoClient通常会自动管理）
        if 'client' in locals():
            client.close()

if __name__ == "__main__":
    test_mongodb_connection()
    print("test_mongodb_connection done")
