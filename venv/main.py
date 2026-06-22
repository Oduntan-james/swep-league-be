from fastapi import FastAPI, HTTPException
from supabase import create_client
from dotenv import load_dotenv
from pydantic import BaseModel
import os

load_dotenv()

app = FastAPI()

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
)

# --- Models ---
class SignupRequest(BaseModel):
    email: str
    password: str
    full_name: str
    department: str

class LoginRequest(BaseModel):
    email: str
    password: str

# --- Routes ---
@app.get("/")
def root():
    return {"message": "Swep League API is live"}

@app.post("/auth/signup")
def signup(data: SignupRequest):
    try:
        # Create auth user
        res = supabase.auth.sign_up({
            "email": data.email,
            "password": data.password
        })
        user_id = res.user.id

        # Save extra info to our users table
        supabase.table("users").insert({
            "id": user_id,
            "email": data.email,
            "full_name": data.full_name,
            "department": data.department,
            "role": "user",
            "wallet_balance": 0
        }).execute()

        return {"message": "Signup successful", "user_id": user_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/auth/login")
def login(data: LoginRequest):
    try:
        res = supabase.auth.sign_in_with_password({
            "email": data.email,
            "password": data.password
        })
        return {
            "message": "Login successful",
            "access_token": res.session.access_token,
            "user_id": res.user.id
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
# --- Wallet Models ---
class DepositRequest(BaseModel):
    user_id: str
    amount: float

# --- Wallet Routes ---
@app.post("/wallet/deposit")
def deposit(data: DepositRequest):
    try:
        # Get current balance
        user = supabase.table("users")\
            .select("wallet_balance")\
            .eq("id", data.user_id)\
            .single()\
            .execute()
        
        current_balance = user.data["wallet_balance"]
        new_balance = current_balance + data.amount

        # Update balance
        supabase.table("users")\
            .update({"wallet_balance": new_balance})\
            .eq("id", data.user_id)\
            .execute()

        # Log transaction
        supabase.table("transactions").insert({
            "user_id": data.user_id,
            "type": "deposit",
            "amount": data.amount
        }).execute()

        return {
            "message": "Deposit successful",
            "new_balance": new_balance
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/wallet/balance/{user_id}")
def get_balance(user_id: str):
    try:
        user = supabase.table("users")\
            .select("wallet_balance, full_name")\
            .eq("id", user_id)\
            .single()\
            .execute()
        
        return {
            "full_name": user.data["full_name"],
            "balance": user.data["wallet_balance"]
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
# --- Match & Prediction Models ---
class CreateMatchRequest(BaseModel):
    team_a: str
    team_b: str
    kickoff_time: str

class PredictionRequest(BaseModel):
    user_id: str
    match_id: str
    pick: str  # team_a / team_b / draw
    stake: float

# --- Match Routes ---
@app.post("/matches/create")
def create_match(data: CreateMatchRequest):
    try:
        # Create teams if they don't exist
        team_a = supabase.table("teams").upsert(
            {"name": data.team_a, "short_code": data.team_a[:3].upper()},
            on_conflict="name"
        ).execute()

        team_b = supabase.table("teams").upsert(
            {"name": data.team_b, "short_code": data.team_b[:3].upper()},
            on_conflict="name"
        ).execute()

        team_a_id = team_a.data[0]["id"]
        team_b_id = team_b.data[0]["id"]

        match = supabase.table("matches").insert({
            "team_a_id": team_a_id,
            "team_b_id": team_b_id,
            "kickoff_time": data.kickoff_time,
            "status": "upcoming"
        }).execute()

        return {
            "message": "Match created",
            "match_id": match.data[0]["id"]
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# --- Prediction Routes ---
@app.post("/predictions/submit")
def submit_prediction(data: PredictionRequest):
    try:
        # Check match exists and is upcoming
        match = supabase.table("matches")\
            .select("status")\
            .eq("id", data.match_id)\
            .single()\
            .execute()

        if match.data["status"] != "upcoming":
            raise HTTPException(status_code=400, detail="Match is not open for predictions")

        # Check user has enough balance
        user = supabase.table("users")\
            .select("wallet_balance")\
            .eq("id", data.user_id)\
            .single()\
            .execute()

        if user.data["wallet_balance"] < data.stake:
            raise HTTPException(status_code=400, detail="Insufficient balance")

        # Deduct stake from balance
        new_balance = user.data["wallet_balance"] - data.stake
        supabase.table("users")\
            .update({"wallet_balance": new_balance})\
            .eq("id", data.user_id)\
            .execute()

        # Save prediction
        prediction = supabase.table("predictions").insert({
            "user_id": data.user_id,
            "match_id": data.match_id,
            "pick": data.pick,
            "stake": data.stake,
            "status": "pending"
        }).execute()

        # Log transaction
        supabase.table("transactions").insert({
            "user_id": data.user_id,
            "type": "stake",
            "amount": data.stake,
            "match_id": data.match_id
        }).execute()

        return {
            "message": "Prediction submitted",
            "prediction_id": prediction.data[0]["id"],
            "remaining_balance": new_balance
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
# --- Live Score Models ---
class UpdateScoreRequest(BaseModel):
    match_id: str
    score_a: int
    score_b: int
    status: str  # upcoming / live / finished

class MatchEventRequest(BaseModel):
    match_id: str
    type: str      # goal / halftime / fulltime / red_card
    team_id: str
    minute: int

# --- Live Score Routes ---
@app.put("/matches/update-score")
def update_score(data: UpdateScoreRequest):
    try:
        supabase.table("matches").update({
            "score_a": data.score_a,
            "score_b": data.score_b,
            "status": data.status
        }).eq("id", data.match_id).execute()

        return {
            "message": "Score updated",
            "score": f"{data.score_a} - {data.score_b}",
            "status": data.status
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/matches/event")
def add_match_event(data: MatchEventRequest):
    try:
        supabase.table("match_events").insert({
            "match_id": data.match_id,
            "type": data.type,
            "team_id": data.team_id,
            "minute": data.minute
        }).execute()

        return {"message": f"Event '{data.type}' logged at minute {data.minute}"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/matches/{match_id}")
def get_match(match_id: str):
    try:
        match = supabase.table("matches")\
            .select("*, team_a:teams!matches_team_a_id_fkey(name), team_b:teams!matches_team_b_id_fkey(name)")\
            .eq("id", match_id)\
            .single()\
            .execute()

        events = supabase.table("match_events")\
            .select("*")\
            .eq("match_id", match_id)\
            .order("minute")\
            .execute()

        return {
            "match": match.data,
            "events": events.data
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/matches")
def get_all_matches():
    try:
        matches = supabase.table("matches")\
            .select("*, team_a:teams!matches_team_a_id_fkey(name), team_b:teams!matches_team_b_id_fkey(name)")\
            .order("kickoff_time")\
            .execute()

        return {"matches": matches.data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
# --- Settlement Model ---
class SettleMatchRequest(BaseModel):
    match_id: str
    result: str  # team_a / team_b / draw

# --- Settlement Route ---
@app.post("/matches/settle")
def settle_match(data: SettleMatchRequest):
    try:
        # Update match result and status
        supabase.table("matches").update({
            "result": data.result,
            "status": "finished"
        }).eq("id", data.match_id).execute()

        # Get all predictions for this match
        all_predictions = supabase.table("predictions")\
            .select("*")\
            .eq("match_id", data.match_id)\
            .eq("status", "pending")\
            .execute()

        if not all_predictions.data:
            return {"message": "No predictions to settle"}

        # Calculate total pool
        total_pool = sum(p["stake"] for p in all_predictions.data)

        # Get winning predictions
        winners = [p for p in all_predictions.data if p["pick"] == data.result]
        losers = [p for p in all_predictions.data if p["pick"] != data.result]

        # If no winners - refund everyone
        if not winners:
            for p in all_predictions.data:
                # Refund stake
                user = supabase.table("users")\
                    .select("wallet_balance")\
                    .eq("id", p["user_id"])\
                    .single()\
                    .execute()

                new_balance = user.data["wallet_balance"] + p["stake"]

                supabase.table("users")\
                    .update({"wallet_balance": new_balance})\
                    .eq("id", p["user_id"])\
                    .execute()

                supabase.table("predictions")\
                    .update({"status": "refunded"})\
                    .eq("id", p["id"])\
                    .execute()

                supabase.table("transactions").insert({
                    "user_id": p["user_id"],
                    "type": "refund",
                    "amount": p["stake"],
                    "match_id": data.match_id
                }).execute()

            return {
                "message": "No winners — everyone refunded",
                "total_pool": total_pool
            }

        # Calculate winning side total
        winning_pool = sum(p["stake"] for p in winners)

        # Pay out winners
        payouts = []
        for p in winners:
            payout = (p["stake"] / winning_pool) * total_pool

            user = supabase.table("users")\
                .select("wallet_balance")\
                .eq("id", p["user_id"])\
                .single()\
                .execute()

            new_balance = user.data["wallet_balance"] + payout

            supabase.table("users")\
                .update({"wallet_balance": new_balance})\
                .eq("id", p["user_id"])\
                .execute()

            supabase.table("predictions")\
                .update({"status": "won", "payout": payout})\
                .eq("id", p["id"])\
                .execute()

            supabase.table("transactions").insert({
                "user_id": p["user_id"],
                "type": "payout",
                "amount": payout,
                "match_id": data.match_id
            }).execute()

            payouts.append({
                "user_id": p["user_id"],
                "staked": p["stake"],
                "payout": payout
            })

        # Mark losers
        for p in losers:
            supabase.table("predictions")\
                .update({"status": "lost"})\
                .eq("id", p["id"])\
                .execute()

        return {
            "message": "Match settled",
            "result": data.result,
            "total_pool": total_pool,
            "winners": len(winners),
            "losers": len(losers),
            "payouts": payouts
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))