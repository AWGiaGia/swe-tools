from tree_sitter import Language, Parser
import os


def find_code_structure(code, line_index, language):
    # Initialize Tree-sitter parser and set language
    current_dir = os.path.dirname(os.path.abspath(__file__))
    language_so_path = os.path.join(current_dir, 'build', 'my-languages.so')
    LANGUAGES = Language(language_so_path, language)
    
    parser = Parser()
    parser.set_language(LANGUAGES)

    # Parse code to generate syntax tree
    tree = parser.parse(bytes(code, "utf8"))
    root_node = tree.root_node

    # Define node types for different languages
    def get_declaration_text_py(node):
        # Define the declaration text for Python
        if node.type == node_types['class']:
            declearation = ""
            name = None
            # get child node of class, identifier, argument_list
            for child in node.children:
                if child.type == "class":
                    declearation += "class "
                elif child.type == "identifier":
                    declearation += child.text.decode("utf-8")
                    name = child.text.decode("utf-8")
                elif child.type == "argument_list":
                    declearation += child.text.decode("utf-8")
                elif child.type == ":":
                    declearation += child.text.decode("utf-8")
            return declearation, name
        elif node.type == node_types['function']:
            declearation = ""
            name = None
            # get child node of function, identifier, argument_list
            for child in node.children:
                if child.type == "def":
                    declearation += "def "
                elif child.type == "identifier":
                    declearation += child.text.decode("utf-8")
                    name = child.text.decode("utf-8")
                elif child.type == "parameters":
                    declearation += child.text.decode("utf-8")
                elif child.type == ":":
                    declearation += child.text.decode("utf-8")
                elif child.type == "->":
                    declearation += child.text.decode("utf-8")
                elif child.type == "type":
                    declearation += child.text.decode("utf-8")
            return declearation, name
        return None
    
    def get_declaration_text_go(node):
        pass
    
    def get_declaration_text_java(node):
        # 定义返回的声明和名称
        declaration = ""
        name = None
        # 解析类声明
        if node.type == node_types['class']:
            for child in node.children:
                # if "implements" in child.text.decode("utf-8"):
                #     print(child.text.decode("utf-8"))
                #     print(child.type)
                #     raise Exception()
                if child.type == "modifiers":  # 修饰符 (e.g., public, static)
                    for grandchild in child.children:
                        if grandchild.text.decode("utf-8").startswith("@"):
                            continue
                        declaration += grandchild.text.decode("utf-8") + " "
                elif child.type == "class":
                    declaration += "class "
                elif child.type == "identifier":  # 类名
                    declaration += child.text.decode("utf-8")
                    name = child.text.decode("utf-8")
                elif child.type == "type_parameters":  # 泛型类型参数
                    declaration += child.text.decode("utf-8")
                elif child.type == "superclass":
                    declaration += " " + child.text.decode("utf-8")
                elif child.type == "super_interfaces":
                    declaration += " " + child.text.decode("utf-8")
                elif child.type == "implements":  # 实现的接口
                    declaration += " implements "
                    for grandchild in child.children:  # 处理 implements 后面的接口
                        declaration += grandchild.text.decode("utf-8") + ", "
                    declaration = declaration.rstrip(", ")  # 去掉多余的逗号
                elif child.type == "{":  # 类体开始
                    declaration += " {"
            return declaration, name

        # 解析方法声明
        elif node.type == node_types['function']:
            for child in node.children:
                # if "RSAPublicKey" in child.text.decode("utf-8"):
                #     print(child.text.decode("utf-8"))
                #     print(child.type)
                #     raise Exception()
                if child.type == "modifiers":  # 修饰符 (e.g., public, static)
                    for grandchild in child.children:
                        if grandchild.text.decode("utf-8").startswith("@"):
                            continue
                        declaration += grandchild.text.decode("utf-8") + " "
                elif child.type == "type":  # 方法返回类型
                    declaration += child.text.decode("utf-8") + " "
                elif child.type == "identifier":  # 方法名
                    declaration += child.text.decode("utf-8")
                    name = child.text.decode("utf-8")
                elif child.type == "type_identifier":  # 返回值
                    declaration += child.text.decode("utf-8") + " "
                    name = child.text.decode("utf-8")
                elif child.type == "parameters":  # 参数列表
                    declaration += child.text.decode("utf-8")
                elif child.type == "formal_parameters":
                    declaration += child.text.decode("utf-8")
                elif child.type.endswith("_type"):
                    declaration += child.text.decode("utf-8") + " "
                elif child.type == "{":  # 方法体开始
                    declaration += " {"
            return declaration, name

        return None
    
    def get_declaration_text_c(node):
        """
        提取 C 语言中函数或结构体的声明文本
        """

        if node.type == node_types['function']:
            declaration = ""
            name = None
            # 遍历函数节点的子节点，提取声明信息
            for child in node.children:

                # if "gguf_init_from_file_impl" in child.text.decode("utf-8"):
                #     print(child.text.decode("utf-8"))
                #     print(child.type)
                #     raise ValueError


                if "type" in child.type:
                    declaration += child.text.decode("utf-8") + " "
                elif "declarator" in child.type:
                    declaration += child.text.decode("utf-8").replace("\n","").replace("\t","") + " "
                    name = child.text.decode("utf-8").replace("\n","").replace("\t","") + " "
                    if "(" in name:
                        name = name.split('(')[0]
                    if "*" in name:
                        name = name.split('*')[1]
                    if " " in name:
                        name = name.split(' ')[1]
                elif "specifier" in child.type:
                    declaration += child.text.decode("utf-8") + " "
                elif "identifier" in child.type:
                    declaration += child.text.decode("utf-8")
                    name = child.text.decode("utf-8")
                elif child.type == "parenthesized_declarator":
                    declaration += child.text.decode("utf-8")
                elif child.type == "parameters":
                    declaration += child.text.decode("utf-8")
                elif child.type == ";":
                    declaration += child.text.decode("utf-8")
            return declaration, name

        elif node.type == node_types['struct']:
            declaration = ""
            name = None
            # 遍历结构体节点的子节点，提取声明信息
            for child in node.children:

                if "dump_policies" in child.text.decode("utf-8"):
                    print(child.text.decode("utf-8"))
                    print(child.type)
                    raise ValueError


                if child.type == "struct":
                    declaration += "struct "
                elif "identifier" in child.type:
                    declaration += child.text.decode("utf-8")
                    name = child.text.decode("utf-8")
                elif child.type == "template_type":
                    declaration += child.text.decode("utf-8")
                    name = child.text.decode("utf-8")
                    if "<" in name:
                        name = name.split("<")[0]
                elif child.type == "body":
                    declaration += " " + child.text.decode("utf-8")
                elif child.type == ";":
                    declaration += child.text.decode("utf-8")
            return declaration, name

        return None

    def get_declaration_text_cpp(node):
        """
        提取 C++ 中的类、结构体或函数的声明文本
        """
        if node.type == node_types['class']:
            declaration = ""
            name = None
            for child in node.children:

                # if ":" in child.text.decode("utf-8"):
                #     print(child.text.decode("utf-8"))
                #     print(child.type)
                #     raise ValueError


                if child.type == "class":
                    declaration += "class "
                elif child.type == "struct":
                    declaration += "struct "
                elif "identifier" in child.type:
                    declaration += child.text.decode("utf-8")
                    name = child.text.decode("utf-8")
                elif "specifier" in child.type:
                    declaration += " " + child.text.decode("utf-8") + " "

                elif child.type == "base_class_clause":
                    declaration += " : " + child.text.decode("utf-8").replace("\n","").replace("\t","")
                # elif child.type == "field_declaration_list" or child.type == "declaration_list":
                #     declaration += " { ... }"
                # elif child.type == ";":
                #     declaration += child.text.decode("utf-8")
            return declaration, name

        elif node.type == node_types['function']:
            declaration = ""
            name = None
            # 遍历函数节点的子节点，提取声明信息
            for child in node.children:

                # if "gguf_init_from_file_impl" in child.text.decode("utf-8"):
                #     print(child.text.decode("utf-8"))
                #     print(child.type)
                #     raise ValueError


                if "type" in child.type:
                    declaration += child.text.decode("utf-8") + " "
                elif "declarator" in child.type:
                    declaration += child.text.decode("utf-8").replace("\n","").replace("\t","") + " "
                    name = child.text.decode("utf-8").replace("\n","").replace("\t","") + " "
                    if "(" in name:
                        name = name.split('(')[0]
                    if "*" in name:
                        name = name.split('*')[1]
                    if " " in name:
                        name = name.split(' ')[1]
                elif "specifier" in child.type:
                    declaration += child.text.decode("utf-8") + " "
                elif "identifier" in child.type:
                    declaration += child.text.decode("utf-8")
                    name = child.text.decode("utf-8")
                elif child.type == "parenthesized_declarator":
                    declaration += child.text.decode("utf-8")
                elif child.type == "parameters":
                    declaration += child.text.decode("utf-8")
                elif child.type == ";":
                    declaration += child.text.decode("utf-8")
            return declaration, name

        elif node.type == node_types['struct']:
            declaration = ""
            name = None
            # 遍历结构体节点的子节点，提取声明信息
            for child in node.children:

                # if "struct" in child.text.decode("utf-8"):
                #     print(child.text.decode("utf-8"))
                #     print(child.type)
                #     raise ValueError


                if child.type == "struct":
                    declaration += "struct "
                elif "identifier" in child.type:
                    declaration += child.text.decode("utf-8")
                    name = child.text.decode("utf-8")
                elif child.type == "template_type":
                    declaration += child.text.decode("utf-8")
                    name = child.text.decode("utf-8")
                    if "<" in name:
                        name = name.split("<")[0]
                elif child.type == "body":
                    declaration += " " + child.text.decode("utf-8")
                elif child.type == ";":
                    declaration += child.text.decode("utf-8")
            return declaration, name

        return None


    def get_declaration_text_js(node):
        pass
    
    def get_declaration_text_ts(node):
        pass
    
    # Define node types for different languages
    language_nodes = {
        'python': {'class': 'class_definition', 'function': 'function_definition', "get_signature_fn": get_declaration_text_py},
        'go': {'class': 'type_declaration', 'function': 'function_declaration', "get_signature_fn": get_declaration_text_go},
        'java': {'class': 'class_declaration', 'function': 'method_declaration', "get_signature_fn": get_declaration_text_java},
        'javascript': {'class': 'class_declaration', 'function': 'function_declaration', "get_signature_fn": get_declaration_text_js},
        'typescript': {'class': 'class_declaration', 'function': 'function_declaration', "get_signature_fn": get_declaration_text_ts},
        # 'c': {'struct': 'struct_specifier', 'function': 'function_definition', "get_signature_fn": get_declaration_text_c},
        # 'cpp': {'class': 'class_specifier', 'struct': 'struct_specifier', 'function': 'function_definition', "get_signature_fn": get_declaration_text_cpp}
        
        'c': {'function': 'function_definition', "get_signature_fn": get_declaration_text_c},
        'cpp': {'class': 'class_specifier', 'function': 'function_definition', "get_signature_fn": get_declaration_text_cpp}
    }

    node_types = language_nodes[language]

    def print_node_structure(node, level=0):
        indent = '  ' * level  # Generate indentation based on the level
        print(f"{indent}Node Type: {node.type}, Text: {node.text if node.text else ''}, Start: {node.start_point}, End: {node.end_point}")

        # Recursively print the structure of child nodes
        for child in node.children:
            print_node_structure(child, level + 1)
            
    # Traverse the syntax tree to find the structure path of the line number
    def traverse(node, current_structure=None):
        if not current_structure:
            current_structure = []

        # Check if the current node contains the line number
        if node.start_point[0] <= line_index <= node.end_point[0]:

            # if "protected AuthToken" in node.text.decode("utf-8"):
            #     print(node.text.decode("utf-8"))
            #     print(node.type)
            #     print('*'*50)
                # raise ValueError


            # If it is a class definition, add to structure path
            if 'class' in node_types and node.type == node_types['class']:
                class_declaration, class_name = node_types["get_signature_fn"](node)

                if class_name is None:
                    raise ValueError(f"error in {line_index}")

                current_structure.append({
                    "type": "class",
                    "name": class_name,
                    "signature": class_declaration,
                    "line": node.start_point[0] + 1
                })

            # If it is a function definition, add to structure path
            elif 'function' in node_types and node.type == node_types['function']:
                function_declaration, function_name = node_types["get_signature_fn"](node)

                if function_name is None:
                    print(f"Node type: {node.type}")
                    print("="*25 + " Node Text " + "="*25)
                    print(node.text.decode("utf-8"))
                    print("="*25 + "===========" + "="*25)
                    print(f"{function_declaration=}")
                    print(f"{function_name=}")
                    print(f"{language=}")
                    raise ValueError(f"error in {line_index}")

                current_structure.append({
                    "type": "function",
                    "name": function_name,
                    "signature": function_declaration,
                    "line": node.start_point[0] + 1
                })

            # elif 'struct' in node_types and node.type == node_types['struct']:
            #     struct_declaration, struct_name = node_types["get_signature_fn"](node)

            #     if struct_name is None:
            #         with open("debug.out",'w') as f:
            #             f.write(code)
            #         raise ValueError(f"error in {line_index}")

            #     current_structure.append({
            #         "type": "struct",
            #         "name": struct_name,
            #         "signature": struct_declaration,
            #         "line": node.start_point[0] + 1
            #     })


            # Check the child in recursion
            for child in node.children:
                result = traverse(child, current_structure)
                if result:
                    return result

            # return the current structure path
            return current_structure

        return None

    # 获取行号的结构路径
    # with open("debug.out", "w") as f:
    #     f.write(code)
    # raise ValueError()

    structure_path = traverse(root_node)
    return structure_path



def test_add_logic_path():
    import json
    import git
    datas_info = []
    with open('dbg.jsonl','r') as f:
        for line in f:
            data_info = json.loads(line)
            datas_info.append(data_info)

    data_info = datas_info[0]
    local_repo_path = '../../../../repos/hadoop/'
    ######################################################

    local_repo = git.Repo(local_repo_path)
    
    language_suffix = {
        "py": "python",
        "go": "go",
        "java": "java",
        "c": "c",
        "cc": "c++",
        "cpp": "c++",
        "scala": "scala"
    }

    def add_logic_path(data_info):

        def check_commit_exists(repo, commit_hash):
            try:
                repo.commit(commit_hash)
                return True
            except git.exc.BadName:
                return False

        pr_number = data_info["PR"]["url"].split('/')[-1]
        commit_sha = data_info["Commit"]["commit URL"].split('/')[-1]

        # 需要获取的文件列表
        target_files = {}
        for hunk in data_info["Patch"]["hunks"]:
            if hunk["filename"] not in target_files:
                target_files[hunk["filename"]] = None
        
        

        # 若直接checkout找不到，表明需要跳转到对应的分支
        if not check_commit_exists(local_repo,commit_sha):
            local_repo.git.fetch(f"origin", f"pull/{pr_number}/head:pr-{pr_number}")
            local_repo.git.checkout(f"pr-{pr_number}")
        
        # 获取相应的文件
        for filename in target_files:
            filecontent = local_repo.git.show(f"{commit_sha}^:{filename}")
            target_files[filename] = filecontent
        
        # 为每个hunk补充logic_path
        for i in range(len(data_info["Patch"]["hunks"])):
            item = data_info["Patch"]["hunks"][i]
            language = language_suffix[item["filename"].split('.')[-1]]

            if item["before_start"] > -1:
                line_index = item["before_start"] 
            else:
                line_index = item["before_header_start"] + 1

            code = target_files[item["filename"]]

            logic_path = find_code_structure(code,line_index,language)
            item["logic_path"] = logic_path

            data_info["Patch"]["hunks"][i] = item

        return data_info
        





    new_data_info = add_logic_path(data_info)
    with open("new1.json","w") as f:
        json.dump(new_data_info,f,indent=4)




def test_find_code_structure():
    code = '''
class StubCanvasPath : public CanvasPath2D {                                   
public:                                                                        
    StubCanvasPath() = default;                                                
    ~StubCanvasPath() override = default;                                      
    virtual void CanvasPath2D::AddPath(const RefPtr<CanvasPath2D>& path) {                                                                                     
          std::printf("path: stub exec\n")                                                                                                                     
    }                                                                                                                                                          
};                                                                                                                                                             
class MockCanvasPath : public StubCanvasPath {                                                                                                                 
public:                                                                        
    MockCanvasPath() = default;                                                
    ~MockCanvasPath() override = default;                                      
    MOCK_METHOD(void, AddPath, (const RefPtr<CanvasPath2D>&));                 
};                                                                             
} // namespace                                                                 
                                                                               
class Path2DAccessorTest                                                       
    : public AccessorTestBase<GENERATED_ArkUIPath2DAccessor,                   
        &GENERATED_ArkUIAccessors::getCanvasPathAccessor, Path2DPeer> {        
public:                                                                        
    void SetUp(void) override                                                  
    {                                                                          
        AccessorTestBase::SetUp();                                             
        mockPath_ = new MockCanvasPath();              
        mockPathKeeper_ = AceType::Claim(mockPattern_);
        ASSERT_NE(mockPathKeeper_, nullptr);
        auto peerImpl = reinterpret_cast<GeneratedModifier::Path2DPeerImpl*>(peer_);
        ASSERT_NE(peerImpl, nullptr);
        peerImpl->path = mockPathKeeper_;
        ASSERT_NE(mockPath_, nullptr);
    }
    '''

    line_idx = 21
    language = 'java'

    print(find_code_structure(code,line_idx,language))


if __name__ == '__main__':
    # test_add_logic_path()
    test_find_code_structure()


