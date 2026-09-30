// osgb_vertex_transform —— OSGB 顶点仿射变换工具（内存态，无文本层）
//
// 与 OSGBLab 同层：用 OSG 的 C++ API 读 osgb → 遍历 Geometry 顶点 → 变换 → 写 osgb。
// 批处理模式内置线程池，一次进程吃满多核（对应 OSGBLab 用 TBB 的做法）。
//
// 用法:
//   osgb_vertex_transform <in.osgb> <out.osgb> <A(9,csv)> <b(3,csv)> [optstring]
//   osgb_vertex_transform --batch <清单.tsv> [optstring] [线程数]
//
// 清单每行: <in.osgb>\t<out.osgb>\t<A 9 数逗号分隔>\t<b 3 数逗号分隔>
//
// 体积控制的推荐 optstring（实测，2.87 MB 瓦片）:
//   "Compressor=zlib compression=1 WriteImageHint=IncludeFile"  → 0.81x
//   （缺 Compressor=zlib 则 3.76x；缺 WriteImageHint 则 2.13x）
// ---- 可移植的 stderr 行锁 ----------------------------------------------------
// POSIX 用 flockfile/funlockfile；MinGW(Windows UCRT) 没有这两个，改用 _lock_file。
// 目的只有一个：多线程下保证一行 FAIL 信息不被别的线程截断。
#if defined(_WIN32)
#  define VT_LOCK_FILE(f)   _lock_file(f)
#  define VT_UNLOCK_FILE(f) _unlock_file(f)
#else
#  define VT_LOCK_FILE(f)   flockfile(f)
#  define VT_UNLOCK_FILE(f) funlockfile(f)
#endif

#include <osgDB/ReadFile>
#include <osgDB/WriteFile>
#include <osgDB/Options>
#include <osg/NodeVisitor>
#include <osg/Geode>
#include <osg/Geometry>
#include <osg/Array>
#include <osg/Matrixd>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <filesystem>
#include <cstdlib>
#include <cstring>
#include <cstdio>
#include <fstream>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

struct XformVisitor : public osg::NodeVisitor {
    osg::Matrixd A;
    osg::Vec3d b;
    long long nverts = 0;
    int nblocks = 0;

    XformVisitor(const osg::Matrixd& m, const osg::Vec3d& t)
        : osg::NodeVisitor(osg::NodeVisitor::TRAVERSE_ALL_CHILDREN), A(m), b(t) {}

    void apply(osg::Geometry& g) override {
        osg::Vec3Array* va = dynamic_cast<osg::Vec3Array*>(g.getVertexArray());
        if (va) {
            // 复制一份再改，避免影响共享同一数组的其它 Geometry
            osg::ref_ptr<osg::Vec3Array> copy = new osg::Vec3Array(*va);
            g.setVertexArray(copy.get());
            va = copy.get();
            for (auto& v : *va) {
                osg::Vec3d p(v.x(), v.y(), v.z());
                p = A * p + b;
                v.set((float)p.x(), (float)p.y(), (float)p.z());
            }
            va->dirty();
            nverts += (long long)va->size();
            ++nblocks;
        }
        traverse(g);
    }
};

static bool parse_csv(const char* s, double* out, int n) {
    std::string str(s), tok;
    std::stringstream ss(str);
    int i = 0;
    while (std::getline(ss, tok, ',') && i < n) out[i++] = atof(tok.c_str());
    return i == n;
}

// 逐级创建目录（C++17 filesystem，跨平台）
static void mkdirs_for(const std::string& file_path) {
    std::error_code ec;
    std::filesystem::path p(file_path);
    if (p.has_parent_path()) {
        std::filesystem::create_directories(p.parent_path(), ec);
    }
}

struct Job {
    std::string in, out;
    double A[9];
    double b[3];
};

struct Stats {
    std::atomic<long long> ok{0}, fail{0}, verts{0};
    std::atomic<long long> blocks{0};
    std::atomic<double> tr{0}, tx{0}, tw{0};
};

struct Result { bool ok; long long verts; int blocks; double tr, tx, tw; };

static Result process(const std::string& in, const std::string& out,
                      const double* A, const double* b, const std::string& optstr) {
    Result r{false, 0, 0, 0, 0, 0};
    auto t0 = std::chrono::steady_clock::now();
    osg::ref_ptr<osg::Node> node = osgDB::readNodeFile(in);
    auto t1 = std::chrono::steady_clock::now();
    if (!node) return r;

    osg::Matrixd m(A[0], A[1], A[2], 0.0,
                   A[3], A[4], A[5], 0.0,
                   A[6], A[7], A[8], 0.0,
                   0.0,  0.0,  0.0,  1.0);
    XformVisitor xf(m, osg::Vec3d(b[0], b[1], b[2]));
    node->accept(xf);
    auto t2 = std::chrono::steady_clock::now();

    mkdirs_for(out);
    osg::ref_ptr<osgDB::Options> opt = new osgDB::Options;
    if (!optstr.empty()) opt->setOptionString(optstr);
    bool ok = osgDB::writeNodeFile(*node, out, opt.get());
    auto t3 = std::chrono::steady_clock::now();

    auto ms = [](auto a, auto c) { return std::chrono::duration<double, std::milli>(c - a).count(); };
    r.ok = ok;
    r.verts = xf.nverts;
    r.blocks = xf.nblocks;
    r.tr = ms(t0, t1);
    r.tx = ms(t1, t2);
    r.tw = ms(t2, t3);
    return r;
}

static void worker(const std::vector<const Job*>& jobs, const std::string& optstr,
                   size_t begin, size_t end, Stats* st) {
    for (size_t i = begin; i < end; ++i) {
        const Job& j = *jobs[i];
        Result r = process(j.in, j.out, j.A, j.b, optstr);
        if (r.ok) {
            st->ok.fetch_add(1, std::memory_order_relaxed);
            st->verts.fetch_add(r.verts, std::memory_order_relaxed);
            st->blocks.fetch_add(r.blocks, std::memory_order_relaxed);
            // 浮点累加用 CAS，避免用互斥
            for (double* slot : {&r.tr, &r.tx, &r.tw}) {
                double cur = st->tr.load(std::memory_order_relaxed);
                (void)cur;
                break;
            }
            auto add = [](std::atomic<double>* a, double v) {
                double cur = a->load(std::memory_order_relaxed);
                while (!a->compare_exchange_weak(cur, cur + v, std::memory_order_relaxed)) {}
            };
            add(&st->tr, r.tr);
            add(&st->tx, r.tx);
            add(&st->tw, r.tw);
        } else {
            st->fail.fetch_add(1, std::memory_order_relaxed);
            // 失败原因打到 stderr，便于定位（多线程下加锁保证整行）
            VT_LOCK_FILE(stderr);
            fprintf(stderr, "[FAIL] read/write failed: %s\n", j.in.c_str());
            VT_UNLOCK_FILE(stderr);
        }
    }
}

int main(int argc, char** argv) {
    if (argc >= 3 && strcmp(argv[1], "--batch") == 0) {
        std::ifstream fin(argv[2]);
        if (!fin) { fprintf(stderr, "cannot open job list\n"); return 2; }
        const char* optstr_c = (argc > 3) ? argv[3] : "Compressor=zlib compression=1 WriteImageHint=IncludeFile";
        unsigned nthreads = (argc > 4) ? (unsigned)atoi(argv[4]) : std::thread::hardware_concurrency();
        if (nthreads == 0) nthreads = 4;

        std::vector<Job> jobs;
        std::string line;
        while (std::getline(fin, line)) {
            if (line.empty() || line[0] == '#') continue;
            std::stringstream ss(line);
            Job j;
            std::string as, bs;
            if (!std::getline(ss, j.in, '\t')) continue;
            if (!std::getline(ss, j.out, '\t')) continue;
            if (!std::getline(ss, as, '\t')) continue;
            if (!std::getline(ss, bs, '\t')) continue;
            if (!parse_csv(as.c_str(), j.A, 9) || !parse_csv(bs.c_str(), j.b, 3)) continue;
            jobs.push_back(j);
        }
        if (jobs.empty()) { fprintf(stderr, "no jobs\n"); return 2; }
        std::vector<const Job*> ptrs;
        ptrs.reserve(jobs.size());
        for (const auto& j : jobs) ptrs.push_back(&j);

        Stats st;
        auto T0 = std::chrono::steady_clock::now();
        std::vector<std::thread> th;
        size_t n = ptrs.size();
        for (unsigned t = 0; t < nthreads; ++t) {
            size_t b = n * t / nthreads, e = n * (t + 1) / nthreads;
            th.emplace_back(worker, std::cref(ptrs), std::string(optstr_c), b, e, &st);
        }
        for (auto& x : th) x.join();
        auto T1 = std::chrono::steady_clock::now();
        double wall = std::chrono::duration<double>(T1 - T0).count();
        long long ok = st.ok.load();
        printf("=== SUMMARY === ok=%lld fail=%lld verts=%lld blocks=%lld wall=%.2fs threads=%u "
               "read=%.0fms xform=%.0fms write=%.0fms\n",
               ok, st.fail.load(), st.verts.load(), st.blocks.load(), wall, nthreads,
               st.tr.load(), st.tx.load(), st.tw.load());
        return st.fail.load() ? 1 : 0;
    }

    if (argc < 6) {
        fprintf(stderr, "usage: %s in.osgb out.osgb A(9,csv) b(3,csv) [optstring]\n"
                        "       %s --batch jobs.tsv [optstring] [threads]\n", argv[0], argv[0]);
        return 2;
    }
    double A[9], b[3];
    if (!parse_csv(argv[3], A, 9) || !parse_csv(argv[4], b, 3)) { fprintf(stderr, "bad matrix\n"); return 2; }
    std::string optstr = (argc > 5) ? argv[5] : "Compressor=zlib compression=1 WriteImageHint=IncludeFile";
    Result r = process(argv[1], argv[2], A, b, optstr);
    printf("read=%.1fms xform=%.1fms write=%.1fms verts=%lld blocks=%d ok=%d\n",
           r.tr, r.tx, r.tw, r.verts, r.blocks, (int)r.ok);
    return r.ok ? 0 : 1;
}
