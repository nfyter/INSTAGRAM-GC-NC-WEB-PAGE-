
@app.route("/stream")
def stream(): return Response(stream_logs(), mimetype='text/event-stream')

if __name__=="__main__":
    print("Run this app: python app.py (open http://127.0.0.1:5000)")
    app.run(host="0.0.0.0", threaded=True)

    
