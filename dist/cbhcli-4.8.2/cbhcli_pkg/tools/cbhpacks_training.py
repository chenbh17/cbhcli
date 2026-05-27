"""cbhpacks 模型训练工具 - model_training 模块封装

三个工具类: BinaryModelTool / UnsModelTool / LinearModelTool
每次执行自动保存可复现的Python源码脚本到输出目录。
"""
import os
import json
import joblib
import pandas as pd
from cbhcli_pkg.tools.registry import BaseTool, ToolResult

_MODEL_TYPE_MAP = {"lr_model.pkl": "lr", "xgb_model.pkl": "xgb", "lgbm_model.pkl": "lgbm",
                   "mlp_model.h5": "keras", "svm_model.pkl": "svm", "rdf_model.pkl": "rdf"}

# 模型类型 → 特征重要性文件映射
_WEIGHT_FILE_MAP = {"lr": "lr_coef.xlsx", "xgb": "xgb_imp.xlsx", "lgbm": "lgbm_imp.xlsx",
                    "keras": "mlp_weight.csv", "svm": "svm_imp.xlsx", "rdf": "rdf_imp.xlsx"}


def _load_saved_model(model_path):
    """从 model_path 加载已保存的模型和特征重要性。

    Returns:
        (clf, model_type, cols_weight) 或 (None, None, None)
    """
    for fname, mtype in _MODEL_TYPE_MAP.items():
        fpath = os.path.join(model_path, fname)
        if os.path.exists(fpath) and fname.endswith(".pkl"):
            clf = joblib.load(fpath)
            # 加载对应的特征重要性文件
            cols_weight = None
            weight_fname = _WEIGHT_FILE_MAP.get(mtype)
            if weight_fname:
                weight_path = os.path.join(model_path, weight_fname)
                if os.path.exists(weight_path):
                    if weight_fname.endswith(".xlsx"):
                        cols_weight = pd.read_excel(weight_path)
                    elif weight_fname.endswith(".csv"):
                        cols_weight = pd.read_csv(weight_path)
            return clf, mtype, cols_weight
    return None, None, None


def save_script(output_dir, filename, code):
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, filename), 'w', encoding='utf-8') as f:
        f.write(code)


class BinaryModelTool(BaseTool):
    @property
    def name(self): return "cbhpacks_binary_model"

    @property
    def description(self):
        return ("cbhpacks 二分类模型训练工具 - 训练/调参/评估报告。支持LR/XGB/LGBM/MLP/SVM/RDF。\n"
                "每次执行自动保存可复现源码。调参/报告方法自动从pkl加载已有模型。\n\n"
                "【method】lr_fit/xgb_fit/lgbm_fit/mlp_fit/svm_fit/rdf_fit/para_adj_gs/para_adj_bs/report")

    @property
    def parameters(self):
        return {"type": "object", "properties": {
            "method": {"type": "string", "enum": ["lr_fit","xgb_fit","lgbm_fit","mlp_fit","svm_fit","rdf_fit","para_adj_gs","para_adj_bs","report"]},
            "train_csv": {"type": "string"}, "test_csv": {"type": "string"},
            "cols": {"type": "string", "description": "JSON格式"}, "target": {"type": "string", "default": "target"},
            "model_path": {"type": "string", "default": "step6_binary_model"},
            "train_data_path": {"type": "string", "default": "step6_binary_model/datas"},
            "save": {"type": "boolean", "default": False},
            "fit_params": {"type": "string", "description": "JSON格式"},
            "group": {"type": "integer"}, "mth_col": {"type": "string"}, "base_mth": {"type": "integer"},
            "bins_type": {"type": "string", "default": "all"},
            "paras": {"type": "string", "description": "JSON格式"},
            "score_type": {"type": "string", "default": "roc_auc"}, "cv": {"type": "integer", "default": 2},
            "epochs": {"type": "integer", "default": 5}, "batch_size": {"type": "integer", "default": 100},
            "validation_split": {"type": "number", "default": 0.5}
        }, "required": ["method"]}

    def execute(self, **kwargs):
        try:
            method = kwargs.get("method")
            train_csv = kwargs.get("train_csv")
            test_csv = kwargs.get("test_csv")
            cols_str = kwargs.get("cols")
            target = kwargs.get("target", "target")
            model_path = kwargs.get("model_path", "step6_binary_model")
            train_data_path = kwargs.get("train_data_path", "step6_binary_model/datas")
            save = kwargs.get("save", False)
            fit_params = json.loads(kwargs.get("fit_params", "{}")) if isinstance(kwargs.get("fit_params"), str) else kwargs.get("fit_params", {})
            group = kwargs.get("group")
            mth_col = kwargs.get("mth_col")
            base_mth = kwargs.get("base_mth")
            bins_type = kwargs.get("bins_type", "all")
            paras = json.loads(kwargs.get("paras", "{}")) if isinstance(kwargs.get("paras"), str) else kwargs.get("paras", {})
            score_type = kwargs.get("score_type", "roc_auc")
            cv = kwargs.get("cv", 2)
            epochs = kwargs.get("epochs", 5)
            batch_size = kwargs.get("batch_size", 100)
            validation_split = kwargs.get("validation_split", 0.5)

            from cbhpacks.model_training import binary_model

            # 读取数据
            train = test = None
            if train_csv and os.path.exists(train_csv):
                if test_csv and os.path.exists(test_csv):
                    train, test = pd.read_csv(train_csv), pd.read_csv(test_csv)
                else:
                    from sklearn.model_selection import train_test_split
                    train, test = train_test_split(pd.read_csv(train_csv), test_size=0.3, random_state=42)
            elif method in ["lr_fit","xgb_fit","lgbm_fit","mlp_fit","svm_fit","rdf_fit","report"]:
                return ToolResult(success=False, output="", error="fit/report方法需要提供train_csv")

            cols = json.loads(cols_str) if isinstance(cols_str, str) else cols_str

            # 初始化
            if train is not None and test is not None and cols:
                bm = binary_model(train=train, test=test, cols=cols, target=target,
                                  model_path=model_path, train_data_path=train_data_path, save=save)
            elif method in ["para_adj_gs", "para_adj_bs"]:
                bm = binary_model.__new__(binary_model)
                bm.model_path, bm.train_data_path, bm.cols, bm.target, bm.adj = model_path, train_data_path, cols or [], target, "noadj"
            else:
                return ToolResult(success=False, output="", error="需要提供train_csv和cols参数")

            output_files = []
            result_text = ""
            fit_params_str = ", ".join(f"{k}={repr(v)}" for k, v in fit_params.items())

            # ═══ fit 方法 ═══
            if method in ["lr_fit","xgb_fit","lgbm_fit","mlp_fit","svm_fit","rdf_fit"]:
                if method == "lgbm_fit":
                    fit_params.setdefault("verbose", -1)
                    fit_params_str = ", ".join(f"{k}={repr(v)}" for k, v in fit_params.items())

                getattr(bm, method)(**fit_params) if method != "mlp_fit" else bm.mlp_fit(epochs=epochs, batch_size=batch_size, validation_split=validation_split)

                model_type = method.replace("_fit", "")
                output_files += [f"  ✅ {model_type}_*.xlsx/pkl — 模型文件"]
                result_text = f"{method} 训练完成"

                if method == "mlp_fit":
                    script_body = f"bm.mlp_fit(epochs={epochs}, batch_size={batch_size}, validation_split={validation_split})"
                else:
                    script_body = f"bm.{method}({fit_params_str})"

            # ═══ 调参方法 ═══
            elif method == "para_adj_gs":
                if not hasattr(bm, 'clf') or bm.clf is None:
                    clf, mt, cw = _load_saved_model(model_path)
                    if not clf: return ToolResult(success=False, output="", error=f"未找到已训练模型，请先执行fit方法")
                    bm.clf, bm.model_type, bm.cols, bm.target, bm.cols_weight = clf, mt, cols or getattr(clf, 'feature_names_in_', bm.cols), target, cw
                para_dic = bm.para_adj_gs(paras=paras, score_type=score_type, cv=cv)
                output_files += [f"  ✅ {model_path}/{bm.model_type}_gs_adj*.pkl"]
                result_text = f"网格搜索调参完成，最佳参数: {para_dic}"
                script_body = f"bm.para_adj_gs(paras={paras}, score_type='{score_type}', cv={cv})"

            elif method == "para_adj_bs":
                if not hasattr(bm, 'clf') or bm.clf is None:
                    clf, mt, cw = _load_saved_model(model_path)
                    if not clf: return ToolResult(success=False, output="", error=f"未找到已训练模型，请先执行fit方法")
                    bm.clf, bm.model_type, bm.cols, bm.target, bm.cols_weight = clf, mt, cols or getattr(clf, 'feature_names_in_', bm.cols), target, cw
                para_dic = bm.para_adj_bs(paras=paras, score_type=score_type, cv=cv)
                output_files += [f"  ✅ {model_path}/{bm.model_type}_bs_adj*.pkl"]
                result_text = f"贝叶斯搜索调参完成，最佳参数: {para_dic}"
                script_body = f"bm.para_adj_bs(paras={paras}, score_type='{score_type}', cv={cv})"

            # ═══ 报告方法 ═══
            elif method == "report":
                if not group: return ToolResult(success=False, output="", error="report需要group参数")
                if not mth_col or not base_mth: return ToolResult(success=False, output="", error="report需要mth_col和base_mth参数")
                if not hasattr(bm, 'clf') or bm.clf is None:
                    clf, mt, cw = _load_saved_model(model_path)
                    if not clf: return ToolResult(success=False, output="", error=f"未找到已训练模型，请先执行fit方法")
                    bm.clf, bm.model_type, bm.adj, bm.cols_weight = clf, mt, "noadj", cw
                bins_rpt_all, confusion_matrix, fea_bins_report, fea_report = bm.report(
                    group=group, mth_col=mth_col, base_mth=base_mth, bins_type=bins_type)
                output_files += [f"  ✅ confusion_matrix_*.xlsx", f"  ✅ *_full_report.xlsx (含KS/ROC/LIFT图)",
                                 f"  ✅ {train_data_path}/xtest_pred.csv", f"  ✅ bins_rpt_*.xlsx"]
                result_text = f"报告生成完成\n混淆矩阵:\n{confusion_matrix.to_string() if hasattr(confusion_matrix,'to_string') else str(confusion_matrix)}"
                script_body = f"bm.report(group={group}, mth_col='{mth_col}', base_mth={base_mth}, bins_type='{bins_type}')"
            else:
                return ToolResult(success=False, output="", error=f"未知方法: {method}")

            # 保存可复现源码
            if train_csv:
                save_script(model_path, f"run_{method}.py", f'''import pandas as pd
from cbhpacks.model_training import binary_model
train = pd.read_csv("{train_csv}")
test = pd.read_csv("{test_csv or train_csv}")
bm = binary_model(train=train, test=test, cols={cols}, target="{target}", model_path="{model_path}")
{script_body}
print("执行完成")
''')
                output_files.append(f"  ✅ {model_path}/run_{method}.py — 可复现源码")

            return ToolResult(success=True, output=(
                f"📊 cbhpacks_binary_model.{method} 执行完成\n\n"
                f"📁 输出文件:\n" + "\n".join(output_files) + f"\n  📁 输出目录: {model_path}/\n\n"
                f"📋 结果:\n{result_text}"))
        except Exception as e:
            import traceback
            return ToolResult(success=False, output="", error=f"执行失败: {str(e)}\n{traceback.format_exc()}")


class UnsModelTool(BaseTool):
    @property
    def name(self): return "cbhpacks_uns_model"

    @property
    def description(self):
        return "cbhpacks 无监督学习工具 - PCA主成分分析 + KMeans聚类。每次执行自动保存可复现源码。"

    @property
    def parameters(self):
        return {"type": "object", "properties": {
            "method": {"type": "string", "enum": ["pca", "get_keams_cluster", "kmeans"]},
            "csv_path": {"type": "string"}, "cols": {"type": "string", "description": "JSON格式"},
            "target": {"type": "string"}, "mean_key": {"type": "string", "default": "[]"},
            "path": {"type": "string", "default": "step6_uns_model"},
            "var_ratio_cumsum": {"type": "number", "default": 0.8}, "n_clusters": {"type": "integer"}
        }, "required": ["method", "csv_path", "cols"]}

    def execute(self, **kwargs):
        try:
            method = kwargs.get("method")
            csv_path = kwargs.get("csv_path")
            cols = json.loads(kwargs.get("cols")) if isinstance(kwargs.get("cols"), str) else kwargs.get("cols")
            target = kwargs.get("target")
            mean_key = json.loads(kwargs.get("mean_key", "[]")) if isinstance(kwargs.get("mean_key"), str) else kwargs.get("mean_key", [])
            path = kwargs.get("path", "step6_uns_model")
            var_ratio_cumsum = kwargs.get("var_ratio_cumsum", 0.8)
            n_clusters = kwargs.get("n_clusters")

            if not os.path.exists(csv_path):
                return ToolResult(success=False, output="", error=f"文件不存在: {csv_path}")
            df = pd.read_csv(csv_path)

            from cbhpacks.model_training import uns_model
            um = uns_model(df=df, cols=cols, target=target, mean_key=mean_key, path=path)

            output_files = []
            result_text = ""
            script_body = ""

            if method == "pca":
                pca_cols, pca_data, pca_detail = um.pca(var_ratio_cumsum=var_ratio_cumsum)
                output_files += [f"  ✅ {path}/pca_cols.pkl", f"  ✅ {path}/pca_data.csv", f"  ✅ {path}/pca_model.pkl", f"  ✅ {path}/pca_details.csv"]
                result_text = f"主成分数: {len(pca_cols)}, shape: {pca_data.shape}"
                script_body = f"pca_cols, pca_data, pca_detail = um.pca(var_ratio_cumsum={var_ratio_cumsum})\nprint(f'主成分数: {{len(pca_cols)}}')"
            elif method == "get_keams_cluster":
                um.get_keams_cluster()
                output_files += [f"  ✅ {path}/SSE肘部法评估.png", f"  ✅ {path}/轮廓系数评估.png"]
                result_text = "聚类数评估图表已生成"
                script_body = "um.get_keams_cluster()\nprint('图表已保存')"
            elif method == "kmeans":
                if not n_clusters: return ToolResult(success=False, output="", error="kmeans需要n_clusters参数")
                data, kmeans_detail = um.kmeans(n_clusters=n_clusters)
                data.to_csv(os.path.join(path, "kmeans_clustered_data.csv"), index=False)
                output_files += [f"  ✅ {path}/cluster_labels.pkl", f"  ✅ {path}/kmeans_center.xlsx", f"  ✅ {path}/kmeans_clustered_data.csv"]
                result_text = f"聚类完成 (n={n_clusters})\n{kmeans_detail.to_string() if hasattr(kmeans_detail,'to_string') else str(kmeans_detail)}"
                script_body = f"data, detail = um.kmeans(n_clusters={n_clusters})\ndata.to_csv('{path}/kmeans_clustered_data.csv', index=False)"
            else:
                return ToolResult(success=False, output="", error=f"未知方法: {method}")

            save_script(path, f"run_{method}.py", f'''import pandas as pd
from cbhpacks.model_training import uns_model
df = pd.read_csv("{csv_path}")
um = uns_model(df=df, cols={cols}, target={repr(target)}, mean_key={mean_key}, path="{path}")
{script_body}
''')
            output_files.append(f"  ✅ {path}/run_{method}.py — 可复现源码")

            return ToolResult(success=True, output=(
                f"📊 cbhpacks_uns_model.{method} 执行完成\n\n"
                f"📁 输出文件:\n" + "\n".join(output_files) + f"\n\n📋 结果:\n{result_text}"))
        except Exception as e:
            import traceback
            return ToolResult(success=False, output="", error=f"执行失败: {str(e)}\n{traceback.format_exc()}")


class LinearModelTool(BaseTool):
    @property
    def name(self): return "cbhpacks_linear_model"

    @property
    def description(self):
        return "cbhpacks 线性回归工具 - OLS/Logit回归 + 工具变量回归(IV)。每次执行自动保存可复现源码。"

    @property
    def parameters(self):
        return {"type": "object", "properties": {
            "method": {"type": "string", "enum": ["ols", "IV"]},
            "csv_path": {"type": "string"}, "cols": {"type": "string", "description": "JSON格式"},
            "target": {"type": "string"}, "iv_target": {"type": "string"}, "iv_col": {"type": "string"},
            "path": {"type": "string", "default": "step6_linear_model"}
        }, "required": ["method", "csv_path", "cols", "target"]}

    def execute(self, **kwargs):
        try:
            method = kwargs.get("method")
            csv_path = kwargs.get("csv_path")
            cols = json.loads(kwargs.get("cols")) if isinstance(kwargs.get("cols"), str) else kwargs.get("cols")
            target = kwargs.get("target")
            iv_target = kwargs.get("iv_target")
            iv_col = kwargs.get("iv_col")
            path = kwargs.get("path", "step6_linear_model")

            if not os.path.exists(csv_path):
                return ToolResult(success=False, output="", error=f"文件不存在: {csv_path}")
            df = pd.read_csv(csv_path)

            from cbhpacks.model_training import linear_model
            lm = linear_model(df=df, cols=cols, target=target, iv_target=iv_target or target, iv_col=iv_col or "", path=path)

            output_files = []
            result_text = ""
            script_body = ""

            if method == "ols":
                model = lm.ols()
                output_files += [f"  ✅ {path}/{lm.model_name}_*.xlsx", f"  ✅ {path}/{lm.model_name}_model.pkl"]
                result_text = f"回归类型: {lm.model_name}\n{model.summary().as_text()[:2000]}"
                script_body = "model = lm.ols()\nprint(model.summary())"
            elif method == "IV":
                if not iv_target or not iv_col:
                    return ToolResult(success=False, output="", error="IV需要iv_target和iv_col参数")
                ols1, ols2 = lm.IV()
                output_files += [f"  ✅ {path}/iv_ols1_model.pkl", f"  ✅ {path}/iv_ols2_model.pkl"]
                result_text = f"第一阶段:\n{ols1.summary().as_text()[:1000]}\n\n第二阶段:\n{ols2.summary().as_text()[:1000]}"
                script_body = "ols1, ols2 = lm.IV()\nprint(ols1.summary())\nprint(ols2.summary())"
            else:
                return ToolResult(success=False, output="", error=f"未知方法: {method}")

            save_script(path, f"run_{method}.py", f'''import pandas as pd
from cbhpacks.model_training import linear_model
df = pd.read_csv("{csv_path}")
lm = linear_model(df=df, cols={cols}, target="{target}", iv_target="{iv_target or target}", iv_col="{iv_col or ''}", path="{path}")
{script_body}
''')
            output_files.append(f"  ✅ {path}/run_{method}.py — 可复现源码")

            return ToolResult(success=True, output=(
                f"📊 cbhpacks_linear_model.{method} 执行完成\n\n"
                f"📁 输出文件:\n" + "\n".join(output_files) + f"\n\n📋 结果:\n{result_text}"))
        except Exception as e:
            import traceback
            return ToolResult(success=False, output="", error=f"执行失败: {str(e)}\n{traceback.format_exc()}")
